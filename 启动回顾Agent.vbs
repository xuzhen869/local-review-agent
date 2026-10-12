' Review Agent desktop launcher (transitional).
' Double-click to start: no console window, auto-starts backend + main UI + tray pet.
Set fso = CreateObject("Scripting.FileSystemObject")
Set WshShell = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
py = scriptDir & "\backend\.venv\Scripts\python.exe"
script = scriptDir & "\launch.py"
WshShell.CurrentDirectory = scriptDir
' Arg 0 = hidden window; False = don't wait
WshShell.Run """" & py & """ """ & script & """", 0, False
