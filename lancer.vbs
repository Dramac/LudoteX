' Point d'entrée "double-clic" pour démarrer LudoteX sur Windows, sans fenêtre
' console qui reste ouverte à l'écran (utilise pythonw.exe, silencieux).
'
' Utiliser lancer.bat à la place si on veut voir la console (débogage).
'
' Ne fait qu'appeler lancer.py avec l'interpréteur du venv du projet : toute
' la logique (vérifications, uvicorn, tunnel, page HTML, arrêt) est dans
' lancer.py.

Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd /c cd /d """ & Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\")) & """ && .venv\Scripts\pythonw.exe lancer.py", 0, False
