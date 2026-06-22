' Lanza Nexus Mod Installer SIN ventana de consola (con pythonw.exe).
' Doble clic en este archivo: la app se abre directamente, sin consola negra.
Option Explicit
Dim sh, fso, dir, up, candidates, i, pyw
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
up = sh.ExpandEnvironmentStrings("%USERPROFILE%")

' Buscar pythonw.exe (el Python que tiene las dependencias).
candidates = Array(up & "\miniconda3\pythonw.exe", up & "\anaconda3\pythonw.exe")
pyw = ""
For i = 0 To UBound(candidates)
    If fso.FileExists(candidates(i)) Then
        pyw = candidates(i)
        Exit For
    End If
Next
If pyw = "" Then pyw = "pythonw.exe"   ' confiar en el PATH

sh.CurrentDirectory = dir
' 0 = ventana oculta (sin consola); False = no esperar.
sh.Run """" & pyw & """ """ & dir & "\run.py""", 0, False
