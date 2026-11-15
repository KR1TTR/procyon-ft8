; Procyon installer for Windows (NSIS 3). Built by packaging/build.sh:
;   makensis -DVERSION=x.y.z -DSRC=<the Windows zip's folder> -DOUT=<setup.exe> procyon.nsi
; Installs for the current user only (no administrator password), into
; %LOCALAPPDATA%\Programs\Procyon, with Start-menu shortcuts and an
; uninstaller. Settings and logbook live in %LOCALAPPDATA%\Procyon and are
; never touched by installing, updating or uninstalling.

Unicode true
!include "MUI2.nsh"

Name "Procyon ${VERSION}"
OutFile "${OUT}"
InstallDir "$LOCALAPPDATA\Programs\Procyon"
InstallDirRegKey HKCU "Software\Procyon" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
BrandingText "Procyon ${VERSION}"

VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "Procyon"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "FileDescription" "Procyon setup"
VIAddVersionKey "CompanyName" "Justin Stapleton, KR1TTR"
VIAddVersionKey "LegalCopyright" "GNU General Public License v3"

!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Procyon"
!define MUI_ICON "procyon.ico"
!define MUI_UNICON "procyon.ico"
!define MUI_ABORTWARNING

!define MUI_WELCOMEPAGE_TEXT "This installs Procyon ${VERSION}, friendly FT8 and FT4 station software, for you on this computer (no administrator password needed).$\r$\n$\r$\nProcyon needs WSJT-X installed as well (wsjt.sourceforge.io) - it uses its decoder and encoder.$\r$\n$\r$\nIf Procyon is running, press its Quit button first. Your settings and logbook are kept."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${SRC}\LICENSE"
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Start Procyon now"
!define MUI_FINISHPAGE_RUN_FUNCTION StartProcyon
!define MUI_FINISHPAGE_TEXT "Procyon is in your Start menu. It runs without a window of its own: it opens in your web browser, and its Quit button (top right) closes it."
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!define MUI_FINISHPAGE_TEXT "Procyon has been removed. Your settings and logbook are still in $LOCALAPPDATA\Procyon - delete that folder yourself if you don't want them any more."
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "English"

; Procyon's own Python can't be replaced while Procyon is running (Windows
; locks the program file). Moving the folder aside fails in that case, so ask
; the user to quit it first.
!macro MOVE_OLD_PYTHON
  move_python:
  IfFileExists "$INSTDIR\python\*.*" 0 python_done
  RMDir /r "$INSTDIR\python.old"
  ClearErrors
  Rename "$INSTDIR\python" "$INSTDIR\python.old"
  IfErrors 0 python_moved
  MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "Procyon is still running.$\r$\n$\r$\nPress the Quit button in Procyon's page (top right), wait a few seconds, then click Retry." IDRETRY move_python
  Abort
  python_moved:
  RMDir /r "$INSTDIR\python.old"
  python_done:
!macroend

Section "Procyon" SecMain
  SectionIn RO
  SetOutPath "$INSTDIR"
  !insertmacro MOVE_OLD_PYTHON
  RMDir /r "$INSTDIR\procyon"   ; no leftovers from the version before
  RMDir /r "$INSTDIR\hamlib"
  File /r "${SRC}\*"
  File "procyon.ico"
  WriteUninstaller "$INSTDIR\Uninstall Procyon.exe"

  CreateShortCut "$SMPROGRAMS\Procyon.lnk" "$INSTDIR\python\pythonw.exe" "-m procyon" \
      "$INSTDIR\procyon.ico" 0 SW_SHOWNORMAL "" "Procyon - FT8 and FT4 station software"
  CreateShortCut "$SMPROGRAMS\Procyon DEMO ONLY - no radio.lnk" "$INSTDIR\python\pythonw.exe" \
      "-m procyon --demo" "$INSTDIR\procyon.ico" 0 SW_SHOWNORMAL "" \
      "Procyon with a simulated band - no radio, nothing is transmitted"

  WriteRegStr HKCU "Software\Procyon" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "Procyon"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "Justin Stapleton, KR1TTR"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\procyon.ico"
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall Procyon.exe"'
  WriteRegStr HKCU "${UNINST_KEY}" "URLInfoAbout" "https://procyon.kr1ttr.radio"
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize" 80000
SectionEnd

Section "Desktop shortcut" SecDesktop
  SetOutPath "$INSTDIR"
  CreateShortCut "$DESKTOP\Procyon.lnk" "$INSTDIR\python\pythonw.exe" "-m procyon" \
      "$INSTDIR\procyon.ico" 0 SW_SHOWNORMAL "" "Procyon - FT8 and FT4 station software"
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecMain} "Procyon itself, with its own copy of Python, and Start-menu shortcuts."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} "A Procyon icon on your desktop."
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Function StartProcyon
  SetOutPath "$INSTDIR"
  Exec '"$INSTDIR\python\pythonw.exe" -m procyon'
FunctionEnd

; Removes only what the installer put there, by name - never the whole folder,
; in case someone installed into a folder holding other files.
Section "Uninstall"
  !insertmacro MOVE_OLD_PYTHON
  RMDir /r "$INSTDIR\procyon"
  RMDir /r "$INSTDIR\hamlib"
  Delete "$INSTDIR\Procyon.bat"
  Delete "$INSTDIR\Procyon DEMO ONLY - no radio.bat"
  Delete "$INSTDIR\README.txt"
  Delete "$INSTDIR\LICENSE"
  Delete "$INSTDIR\procyon.ico"
  Delete "$INSTDIR\Uninstall Procyon.exe"
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\Procyon.lnk"
  Delete "$SMPROGRAMS\Procyon DEMO ONLY - no radio.lnk"
  Delete "$DESKTOP\Procyon.lnk"
  DeleteRegKey HKCU "${UNINST_KEY}"
  DeleteRegKey HKCU "Software\Procyon"
SectionEnd
