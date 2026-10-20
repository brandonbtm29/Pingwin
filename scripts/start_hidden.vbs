' Starts Pingwin in the background, with no window.
' Its log is in logs\pingwin.log (also on the dashboard's Logs tab).
' Stop it from the dashboard (Status > Stop Pingwin) or with scripts\stop_pingwin.cmd.
' If Pingwin can't start because of a settings problem, a window opens to show what's wrong.
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.Environment("Process")("PINGWIN_HIDDEN") = "1"
shell.Run "cmd /c """"" & dir & "\run_pingwin.cmd""""", 0, False
