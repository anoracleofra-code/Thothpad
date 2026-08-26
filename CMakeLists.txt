# SPDX-FileCopyrightText: 2022-2024 Megan Conkle <megan.conkle@kdemail.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

cmake_minimum_required(VERSION 3.16)
if (POLICY CMP0063)
    cmake_policy(SET CMP0063 NEW)
endif()

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE "Release" CACHE STRING
            "Default build type: Release" FORCE)
endif()

if (NOT BUILD_TESTING)
    set (BUILD_TESTING OFF)
endif()

option(THOTHPAD_INSTRUMENTATION "Compile prose-analysis measurement instrumentation hooks" OFF)

message("Generating makefile for ${CMAKE_BUILD_TYPE} build...")

# KF6 only
set(QT_MAJOR_VERSION 6)
set(QT_MIN_VERSION "6.5.0")
set(KF_MAJOR_VERSION 6)
set(KF_DEP_VERSION "6.0.0")
set(KF_MIN_VERSION "6.0.0")

# use C++20 like KF6 itself
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_CXX_STANDARD_REQUIRED TRUE)

# KDE Application Version, managed by release script
set(RELEASE_SERVICE_VERSION_MAJOR "0")
set(RELEASE_SERVICE_VERSION_MINOR "1")
set(RELEASE_SERVICE_VERSION_MICRO "2")
set(RELEASE_SERVICE_VERSION "${RELEASE_SERVICE_VERSION_MAJOR}.${RELEASE_SERVICE_VERSION_MINOR}.${RELEASE_SERVICE_VERSION_MICRO}")

project(thothpad VERSION ${RELEASE_SERVICE_VERSION} LANGUAGES CXX)

set(THOTHPAD_PACKAGE_VARIANT "Full" CACHE STRING "Release package profile: Core or Full")
set_property(CACHE THOTHPAD_PACKAGE_VARIANT PROPERTY STRINGS Core Full)
if(NOT THOTHPAD_PACKAGE_VARIANT MATCHES "^(Core|Full)$")
    message(FATAL_ERROR "THOTHPAD_PACKAGE_VARIANT must be Core or Full")
endif()
if(THOTHPAD_PACKAGE_VARIANT STREQUAL "Core")
    set(THOTHPAD_PACKAGE_WEBENGINE_POLICY "omitted")
else()
    set(THOTHPAD_PACKAGE_WEBENGINE_POLICY "included")
endif()

find_package(ECM ${KF_MIN_VERSION} REQUIRED NO_MODULE)


# Append to the module path so modules can be overridden from the command line.
list(APPEND CMAKE_MODULE_PATH ${ECM_MODULE_PATH})

set(CMAKE_MODULE_PATH ${ECM_MODULE_PATH})
include(KDEInstallDirs)
include(KDECompilerSettings NO_POLICY_SCOPE)
remove_definitions(-DQT_NO_KEYWORDS)
remove_definitions(-DQT_NO_CAST_TO_ASCII)
remove_definitions(-DQT_NO_CAST_FROM_ASCII)
include(KDECMakeSettings)
include(ECMInstallIcons)
include(ECMAddAppIcon)
include(ECMAddTests)
include(ECMPoQmTools)
include(KDEClangFormat)
include(KDEGitCommitHooks)
include(FeatureSummary)

set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)
set(CMAKE_AUTOUIC ON)

if (IS_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/poqm")
    ecm_install_po_files_as_qm(poqm)
endif()

find_package(KF${KF_MAJOR_VERSION}DocTools ${KF_MIN_VERSION})
set_package_properties(KF${KF_MAJOR_VERSION}DocTools PROPERTIES DESCRIPTION
    "Tools to generate documentation"
    TYPE OPTIONAL
)

set(CMARK_TESTS OFF)
add_subdirectory(3rdparty/cmark-gfm EXCLUDE_FROM_ALL)
add_subdirectory(src)

if(KF${QT_MAJOR_VERSION}DocTools_FOUND)
    # Upstream translated man pages still describe the Ghostwriter command.
    # Keep their sources for attribution, but do not install them under the
    # ThothPad package identity.
    add_subdirectory(doc)
endif()

# Add icon files to the application's source files to have CMake bundle them in the executable.
set(ICONS_FOLDER ${CMAKE_CURRENT_SOURCE_DIR}/resources/icons)

set(ICON_FILES
    ${ICONS_FOLDER}/sc-apps-thothpad.svg
    ${ICONS_FOLDER}/16-apps-thothpad.png
    ${ICONS_FOLDER}/22-apps-thothpad.png
    ${ICONS_FOLDER}/32-apps-thothpad.png
    ${ICONS_FOLDER}/64-apps-thothpad.png
    ${ICONS_FOLDER}/128-apps-thothpad.png
    ${ICONS_FOLDER}/256-apps-thothpad.png
    ${ICONS_FOLDER}/512-apps-thothpad.png
    ${ICONS_FOLDER}/1024-apps-thothpad.png
)

# Windows and macOS bundle generators do not use the 22 px hicolor size.
set(APP_ICON_FILES ${ICON_FILES})
list(REMOVE_ITEM APP_ICON_FILES ${ICONS_FOLDER}/22-apps-thothpad.png)
ecm_add_app_icon(ICONS_SOURCES ICONS ${APP_ICON_FILES})
target_sources(thothpad PRIVATE ${ICONS_SOURCES})

if(BUILD_TESTING)
   add_subdirectory(autotest)
endif()


# See https://cmake.org/cmake/help/v3.15/prop_tgt/MACOSX_BUNDLE_INFO_PLIST.html
if(APPLE)
    string(TIMESTAMP CURRENT_YEAR "%Y")

    set_property(
        TARGET thothpad
        PROPERTY MACOSX_BUNDLE_INFO_PLIST ${CMAKE_CURRENT_SOURCE_DIR}/resources/mac/MacOSBundleInfo.plist.in
    )

    # These are substituted by CMake into MacOSBundleInfo.plist.in.
    set(MACOSX_BUNDLE_DISPLAY_NAME "ThothPad")
    set(MACOSX_BUNDLE_GUI_IDENTIFIER "org.thothpad.ThothPad")
    set(MACOSX_BUNDLE_BUNDLE_NAME "ThothPad")
    set(MACOSX_BUNDLE_INFO_STRING "ThothPad - A prose-aware writing studio")
    set(MACOSX_BUNDLE_LONG_VERSION_STRING "ThothPad ${RELEASE_SERVICE_VERSION}")
    set(MACOSX_BUNDLE_SHORT_VERSION_STRING "${RELEASE_SERVICE_VERSION_MAJOR}.${RELEASE_SERVICE_VERSION_MINOR}")
    set(MACOSX_BUNDLE_BUNDLE_VERSION "${RELEASE_SERVICE_VERSION}")
    set(MACOSX_BUNDLE_COPYRIGHT "2014-${CURRENT_YEAR} the Ghostwriter and ThothPad contributors")
endif()

install(TARGETS thothpad ${KDE_INSTALL_TARGETS_DEFAULT_ARGS})
install(
    FILES
        ${CMAKE_CURRENT_SOURCE_DIR}/COPYING
        ${CMAKE_CURRENT_SOURCE_DIR}/PROVENANCE.md
        ${CMAKE_CURRENT_SOURCE_DIR}/THIRD_PARTY_NOTICES.md
        ${CMAKE_CURRENT_SOURCE_DIR}/writer-engine/THIRD_PARTY.md
    DESTINATION ${KDE_INSTALL_DATADIR}/thothpad
)
install(
    DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/LICENSES/
    DESTINATION ${KDE_INSTALL_DATADIR}/thothpad/LICENSES
)

configure_file(
    ${CMAKE_CURRENT_SOURCE_DIR}/packaging/package-profile.json.in
    ${CMAKE_CURRENT_BINARY_DIR}/thothpad-package-profile.json
    @ONLY
)
install(
    FILES ${CMAKE_CURRENT_BINARY_DIR}/thothpad-package-profile.json
    DESTINATION ${KDE_INSTALL_DATADIR}/thothpad
)

option(THOTHPAD_BUNDLE_ENGINE "Install a prebuilt ThothPad Engine sidecar" OFF)
set(THOTHPAD_ENGINE_DIR "" CACHE PATH "Directory containing the packaged ThothPad Engine")
if(THOTHPAD_BUNDLE_ENGINE)
    if(NOT IS_DIRECTORY "${THOTHPAD_ENGINE_DIR}")
        message(FATAL_ERROR "THOTHPAD_ENGINE_DIR must point to a packaged ThothPad Engine directory")
    endif()
    install(
        DIRECTORY "${THOTHPAD_ENGINE_DIR}/"
        DESTINATION "${KDE_INSTALL_BINDIR}/writer-engine"
    )
endif()

ecm_install_icons(
    ICONS ${ICON_FILES}
    DESTINATION ${KDE_INSTALL_ICONDIR}
    THEME hicolor
)

install(
    PROGRAMS ${CMAKE_CURRENT_SOURCE_DIR}/resources/linux/org.thothpad.ThothPad.desktop
    DESTINATION ${KDE_INSTALL_APPDIR}
)

install(
    FILES ${CMAKE_CURRENT_SOURCE_DIR}/resources/linux/org.thothpad.ThothPad.metainfo.xml
    DESTINATION ${KDE_INSTALL_METAINFODIR}
)

feature_summary(WHAT ALL INCLUDE_QUIET_PACKAGES FATAL_ON_MISSING_REQUIRED_PACKAGES)

file(GLOB_RECURSE ALL_CLANG_FORMAT_SOURCE_FILES src/**/*.cpp src/**/*.h)
kde_clang_format(${ALL_CLANG_FORMAT_SOURCE_FILES})
kde_configure_git_pre_commit_hook(CHECKS CLANG_FORMAT)
