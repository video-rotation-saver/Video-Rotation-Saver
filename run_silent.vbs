' run_silent.vbs
' Launches the PotPlayerRotate daemon with no visible console window.
' Double-click this file, or drop a shortcut to it into your Startup folder
' (Win+R -> shell:startup).
'
' Location assumption: this .vbs lives in the project root, alongside the
' potplayer_rotate/ package and (optionally) a .venv/ folder.

Option Explicit

Dim fso, shell, here, pyw, args, venvPyw
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

here = fso.GetParentFolderName(WScript.ScriptFullName)
venvPyw = here & "\.venv\Scripts\pythonw.exe"

If fso.FileExists(venvPyw) Then
    pyw = venvPyw
Else
    pyw = "pythonw.exe"    ' fall back to system pythonw on PATH
End If

args = """" & pyw & """ -m potplayer_rotate daemon"

' 0 = hidden window, False = don't wait.
shell.CurrentDirectory = here
shell.Run args, 0, False
