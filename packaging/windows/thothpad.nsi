; SPDX-License-Identifier: GPL-3.0-or-later

Unicode true
RequestExecutionLevel user

!include "MUI2.nsh"
!include "x64.nsh"

!ifndef VERSION
  !define VERSION "0.1.2"
!endif
!ifndef STAGE_DIR
  !error "STAGE_DIR is required"
!endif
!ifndef OUTPUT_FILE
  !define OUTPUT_FILE "ThothPad-${VERSION}-setup.exe"
!endif

Name "ThothPad"
OutFile "${OUTPUT_FILE}"
InstallDir "$LOCALAPPDATA\Programs\ThothPad"
InstallDirRegKey HKCU "Software\ThothPad\Studio" "InstallDir"
BrandingText "ThothPad"
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "ThothPad"
VIAddVersionKey "FileDescription" "ThothPad installer"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "GPL-3.0-or-later"

!define MUI_ABORTWARNING
!define MUI_ICON "..\..\resources\windows\thothpad.ico"
!define MUI_UNICON "..\..\resources\windows\thothpad.ico"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\..\COPYING"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "ThothPad" Core
  SectionIn RO
  SetOutPath "$INSTDIR"
  File /r "${STAGE_DIR}\*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\ThothPad\Studio" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ThothPad" "DisplayName" "ThothPad"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ThothPad" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ThothPad" "Publisher" "ThothPad contributors"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ThothPad" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  CreateDirectory "$SMPROGRAMS\ThothPad"
  CreateShortcut "$SMPROGRAMS\ThothPad\ThothPad.lnk" "$INSTDIR\thothpad.exe"
  CreateShortcut "$SMPROGRAMS\ThothPad\Uninstall.lnk" "$INSTDIR\Uninstall.exe"
SectionEnd

Section /o "Desktop shortcut" DesktopShortcut
  CreateShortcut "$DESKTOP\ThothPad.lnk" "$INSTDIR\thothpad.exe"
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\ThothPad.lnk"
  RMDir /r "$SMPROGRAMS\ThothPad"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ThothPad"
  DeleteRegKey HKCU "Software\ThothPad\Studio"
SectionEnd
