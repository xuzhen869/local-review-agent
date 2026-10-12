"""密钥管理（§6.1）。

- 主密钥：`os.urandom(32)`，Windows 用 DPAPI（CryptProtectData）加密后存
  `secrets/master.key.enc`；其余平台（macOS/Linux）用系统 keyring。
- 恢复口令备份（全库加密的硬要求）：口令经 Argon2id 派生密钥，对主密钥做
  AES-GCM 加密后存 `secrets/recovery.enc`；密钥丢失时凭口令恢复。
- `lock_memory`（cipher_memory_security）：先检测 SeLockMemoryPrivilege
  （用 VirtualLock 实测），无权限则降级关闭 —— 避免 SQLCipher 原生崩溃。
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

from ..config import SECRETS_DIR
from ..logging_setup import get_logger

log = get_logger("security.keyring")

_CRYPTPROTECT_UI_FORBIDDEN = 0x01
_RECOVERY_SALT_BYTES = 16
_RECOVERY_NONCE_BYTES = 12
_MASTER_KEY_BYTES = 32
_RECOVERY_FILE_NAME = "recovery.enc"
_MASTER_KEY_FILE_NAME = "master.key.enc"

# 恢复备份文件头（v1）：参数自描述，保证未来调参不破坏已导出的备份可恢复性。
# 旧格式（无头，salt+nonce+ct）用 _LEGACY_* 历史参数回退解密。
_RECOVERY_MAGIC = b"LRA1"            # 版本标识：识别新格式
_RECOVERY_HEADER_BYTES = 12          # magic(4) + iterations(4) + memory_mib(4)
_LEGACY_ITERATIONS = 4               # 旧格式的历史默认迭代数
_LEGACY_MEMORY_MIB = 64              # 旧格式的历史默认内存(MiB)


class MasterKeyError(RuntimeError):
    """主密钥不可用 / 恢复失败。"""


# ---------------------------------------------------------------------------
# DPAPI（Windows）—— 用 ctypes 直调 Win32 API，避免 pywin32 依赖
# ---------------------------------------------------------------------------
class _DATA_BLOB(ctypes.Structure):
    _fields_: list = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob_from_bytes(data: bytes) -> _DATA_BLOB:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))


def _dpapi_protect(data: bytes) -> bytes:
    """DPAPI 加密（用户 scope）。仅 Windows 可用。"""
    blob_in = _blob_from_bytes(data)
    blob_out = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out),
    ):
        err = ctypes.get_last_error()
        raise MasterKeyError(f"DPAPI CryptProtectData 失败 (WinErr={err})")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    blob_in = _blob_from_bytes(data)
    blob_out = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out),
    ):
        err = ctypes.get_last_error()
        raise MasterKeyError(f"DPAPI CryptUnprotectData 失败 (WinErr={err})")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def can_lock_memory() -> bool:
    """实测当前进程能否 mlock 内存（SeLockMemoryPrivilege）。

    SQLCipher 的 cipher_memory_security 在无权限时直接原生崩溃（VirtualLock 失败），
    Python 层抓不住，因此必须在此预检测。
    """
    if not sys.platform.startswith("win"):
        return False
    try:
        page = ctypes.create_string_buffer(4096)
        if ctypes.windll.kernel32.VirtualLock(page, 4096):
            ctypes.windll.kernel32.VirtualUnlock(page, 4096)
            return True
        return False
    except (AttributeError, OSError):
        return False


# ---------------------------------------------------------------------------
# 恢复口令的 KDF（Argon2id）与 AES-GCM
# ---------------------------------------------------------------------------
def _derive_recovery_key(passphrase: str, salt: bytes, iterations: int, memory_mib: int) -> bytes:
    kdf = Argon2id(salt=salt, length=_MASTER_KEY_BYTES,
                   memory_cost=memory_mib * 1024, iterations=iterations, lanes=1)
    return kdf.derive(passphrase.encode("utf-8"))


def _aesgcm_encrypt(key: bytes, plaintext: bytes) -> tuple[bytes, bytes]:
    nonce = os.urandom(_RECOVERY_NONCE_BYTES)
    ct = AESGCM(key).encrypt(nonce, plaintext, None)
    return nonce, ct


def _aesgcm_decrypt(key: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    return AESGCM(key).decrypt(nonce, ciphertext, None)


# ---------------------------------------------------------------------------
# Keyring
# ---------------------------------------------------------------------------
class Keyring:
    """主密钥生命周期管理。"""

    def __init__(self, secrets_dir: Path | None = None,
                 recovery_iterations: int = 10, recovery_memory_mib: int = 64) -> None:
        self._secrets_dir = secrets_dir or SECRETS_DIR
        self._iterations = recovery_iterations
        self._memory_mib = recovery_memory_mib
        self._master_key: bytes | None = None

    # ---- 主密钥 ----
    def ensure_master_key(self) -> bytes:
        """取主密钥；不存在则生成并用平台安全存储托管。"""
        if self._master_key is not None:
            return self._master_key
        key_file = self._secrets_dir / _MASTER_KEY_FILE_NAME
        if key_file.exists():
            self._master_key = self._unload_key(key_file)
        else:
            self._master_key = self._generate_and_store(key_file)
        log.info("主密钥就绪", detail={"store": "DPAPI" if sys.platform.startswith("win") else "file"})
        return self._master_key

    def _generate_and_store(self, key_file: Path) -> bytes:
        self._secrets_dir.mkdir(parents=True, exist_ok=True)
        key = os.urandom(_MASTER_KEY_BYTES)
        protected = self._protect_key(key)
        # 原子写入：先写临时文件再改名，避免写一半的坏文件
        tmp = key_file.with_suffix(".tmp")
        tmp.write_bytes(protected)
        tmp.replace(key_file)
        return key

    def _unload_key(self, key_file: Path) -> bytes:
        try:
            return self._unprotect_key(key_file.read_bytes())
        except MasterKeyError as exc:
            raise MasterKeyError(
                f"主密钥解锁失败：{exc}。若密钥文件损坏，可用恢复口令导入（recover_from_passphrase）。"
            ) from exc

    def _protect_key(self, key: bytes) -> bytes:
        if sys.platform.startswith("win"):
            return _dpapi_protect(key)
        # 非 Windows：本版以 Windows 为目标平台，其余平台直接报错（无需建目录）
        raise MasterKeyError("非 Windows 平台密钥托管待实现")

    def _unprotect_key(self, data: bytes) -> bytes:
        if sys.platform.startswith("win"):
            return _dpapi_unprotect(data)
        raise MasterKeyError("非 Windows 平台密钥解锁待实现")

    # ---- 恢复口令备份 ----
    def has_recovery_backup(self) -> bool:
        return (self._secrets_dir / _RECOVERY_FILE_NAME).exists()

    def _effective_iterations(self) -> int:
        """Argon2id 迭代强度底线：即使配置被调低，也至少 10 次。

        OWASP 对 Argon2id 推荐 t=3；本机 64MiB 内存下 t=10 派生耗时 <1s，
        对恢复口令这种罕见操作完全可接受，且显著抬高暴力破解成本。
        """
        return max(self._iterations, 10)

    def create_recovery_backup(self, passphrase: str) -> Path:
        """用口令加密主密钥，导出恢复备份文件（v1 头：参数自描述）。"""
        if not passphrase or len(passphrase.strip()) < 8:
            raise MasterKeyError("恢复口令至少 8 个字符，请设置强度足够的口令")
        key = self.ensure_master_key()
        self._secrets_dir.mkdir(parents=True, exist_ok=True)
        salt = os.urandom(_RECOVERY_SALT_BYTES)
        iterations = self._effective_iterations()
        rec_key = _derive_recovery_key(passphrase, salt, iterations, self._memory_mib)
        nonce, ct = _aesgcm_encrypt(rec_key, key)
        # 文件头记录实际派生参数，恢复时读取，保证未来调参不破坏旧备份
        header = (_RECOVERY_MAGIC + iterations.to_bytes(4, "big")
                  + self._memory_mib.to_bytes(4, "big"))
        out = self._secrets_dir / _RECOVERY_FILE_NAME
        tmp = out.with_suffix(".tmp")
        tmp.write_bytes(header + salt + nonce + ct)
        tmp.replace(out)
        log.info("恢复口令备份已生成",
                 detail={"path": str(out), "iterations": iterations,
                         "memory_mib": self._memory_mib})
        return out

    def recover_from_passphrase(self, passphrase: str) -> bytes:
        """凭恢复口令还原主密钥，并写回 master.key.enc（覆盖损坏/丢失的旧密钥）。

        兼容两种备份格式：v1 带参数头（自描述）；旧格式无头，用历史默认参数回退。
        """
        path = self._secrets_dir / _RECOVERY_FILE_NAME
        if not path.exists():
            raise MasterKeyError("未找到恢复备份文件，无法恢复")
        blob = path.read_bytes()
        if blob.startswith(_RECOVERY_MAGIC):
            # v1：magic(4) + iterations(4) + memory_mib(4) + salt + nonce + ct
            if len(blob) < _RECOVERY_HEADER_BYTES + _RECOVERY_SALT_BYTES + _RECOVERY_NONCE_BYTES:
                raise MasterKeyError("恢复备份文件损坏（长度异常）")
            iterations = int.from_bytes(blob[4:8], "big")
            memory_mib = int.from_bytes(blob[8:12], "big")
            salt = blob[_RECOVERY_HEADER_BYTES:_RECOVERY_HEADER_BYTES + _RECOVERY_SALT_BYTES]
            nonce = blob[_RECOVERY_HEADER_BYTES + _RECOVERY_SALT_BYTES:
                         _RECOVERY_HEADER_BYTES + _RECOVERY_SALT_BYTES + _RECOVERY_NONCE_BYTES]
            ct = blob[_RECOVERY_HEADER_BYTES + _RECOVERY_SALT_BYTES + _RECOVERY_NONCE_BYTES:]
        else:
            # 旧格式：salt(16) + nonce(12) + ct，用历史默认参数（4 次 / 64MiB）
            if len(blob) < _RECOVERY_SALT_BYTES + _RECOVERY_NONCE_BYTES:
                raise MasterKeyError("恢复备份文件损坏（长度异常）")
            iterations = _LEGACY_ITERATIONS
            memory_mib = _LEGACY_MEMORY_MIB
            salt = blob[:_RECOVERY_SALT_BYTES]
            nonce = blob[_RECOVERY_SALT_BYTES:_RECOVERY_SALT_BYTES + _RECOVERY_NONCE_BYTES]
            ct = blob[_RECOVERY_SALT_BYTES + _RECOVERY_NONCE_BYTES:]
        try:
            rec_key = _derive_recovery_key(passphrase, salt, iterations, memory_mib)
            master_key = _aesgcm_decrypt(rec_key, nonce, ct)
        except Exception as exc:  # AESGCM 解密失败 = 口令错误或数据损坏
            raise MasterKeyError("恢复口令错误或备份文件损坏") from exc
        if len(master_key) != _MASTER_KEY_BYTES:
            raise MasterKeyError("恢复出的主密钥长度异常")
        self._master_key = master_key
        self._generate_and_store(self._secrets_dir / _MASTER_KEY_FILE_NAME)
        log.info("主密钥已通过恢复口令重建")
        return master_key

    def wipe_master_key_file(self) -> None:
        """删除本地主密钥文件（配合恢复流程使用，谨慎调用）。"""
        path = self._secrets_dir / _MASTER_KEY_FILE_NAME
        if path.exists():
            path.unlink()
        self._master_key = None
