' hanjigap tray launcher (no console window).
' Put a shortcut to this file in shell:startup to start hanjigap at Windows sign-in.
' Runs .venv\Scripts\pythonw.exe -m hanjigap tray from this folder (so .env is found).
Option Explicit
Dim fso, sh, root, pyw
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = root & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pyw) Then
  MsgBox "hanjigap: .venv not found." & vbCrLf & "Run 'uv sync' in " & root & " first.", 16, "hanjigap"
  WScript.Quit 1
End If
sh.CurrentDirectory = root
sh.Run """" & pyw & """ -m hanjigap tray", 0, False