# -*- coding: utf-8 -*-
"""密钥恢复备份兼容性测试（P2-5）。

Argon2id 调强（默认 4→10 次）后，恢复备份文件升级为 v1 格式（参数自描述头），
旧格式（无头）用历史默认参数回退。本测试锁定：
1. v1 往返：备份 → 凭口令恢复得到相同主密钥；
2. 旧格式兼容：历史 4 次迭代的无头备份仍可凭口令恢复（不因调参而作废）。
"""
import os

import pytest

from app.security.keyring import (Keyring, _LEGACY_ITERATIONS, _LEGACY_MEMORY_MIB,
                                  _MASTER_KEY_BYTES, _RECOVERY_NONCE_BYTES,
                                  _RECOVERY_SALT_BYTES, _aesgcm_encrypt,
                                  _derive_recovery_key)

_PASSPHRASE = "recover-pass-123"


class TestRecoveryBackup:
    def test_新格式往返(self, tmp_path) -> None:
        k = Keyring(secrets_dir=tmp_path, recovery_iterations=10)
        k.ensure_master_key()
        k.create_recovery_backup(_PASSPHRASE)
        # 重新实例化模拟冷启动，凭口令恢复出同一主密钥
        k2 = Keyring(secrets_dir=tmp_path, recovery_iterations=10)
        assert k2.recover_from_passphrase(_PASSPHRASE) == k.ensure_master_key()

    def test_旧格式无头备份兼容(self, tmp_path) -> None:
        master = os.urandom(_MASTER_KEY_BYTES)
        salt = os.urandom(_RECOVERY_SALT_BYTES)
        rec_key = _derive_recovery_key(
            _PASSPHRASE, salt, _LEGACY_ITERATIONS, _LEGACY_MEMORY_MIB)
        nonce, ct = _aesgcm_encrypt(rec_key, master)
        # 旧格式：salt + nonce + ct，无版本头
        (tmp_path / "recovery.enc").write_bytes(salt + nonce + ct)
        k = Keyring(secrets_dir=tmp_path, recovery_iterations=10)
        assert k.recover_from_passphrase(_PASSPHRASE) == master

    def test_错误口令被拦(self, tmp_path) -> None:
        k = Keyring(secrets_dir=tmp_path, recovery_iterations=10)
        k.ensure_master_key()
        k.create_recovery_backup(_PASSPHRASE)
        k2 = Keyring(secrets_dir=tmp_path, recovery_iterations=10)
        with pytest.raises(Exception):
            k2.recover_from_passphrase("wrong-pass-123")
