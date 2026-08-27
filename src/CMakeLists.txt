# SPDX-FileCopyrightText: 2022-2023 Megan Conkle <megan.conkle@kdemail.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

set(CMAKE_AUTOUIC ON)
set(CMAKE_INCLUDE_CURRENT_DIR ON)

set(UI_FILES settings/preferencesdialog.ui)

add_executable(thothpad ../resources.qrc ../icons.qrc ${UI_FILES})

target_compile_definitions(thothpad PRIVATE APPVERSION="${RELEASE_SERVICE_VERSION}")

target_include_directories(thothpad PUBLIC
    "${PROJECT_BINARY_DIR}"
    "${PROJECT_SOURCE_DIR}"
    "${CMAKE_CURRENT_SOURCE_DIR}"
    "${CMAKE_CURRENT_BINARY_DIR}/../3rdparty/cmark-gfm/src"
)

ecm_create_qm_loader(ghostwriter_QM_LOADER ghostwriter_qt)

target_sources(thothpad PRIVATE
    preview/htmlpreview.h
    editor/actions/inlinemarkuptoggle.cpp
    editor/asynctextwriter.cpp
    editor/markdowndocument.cpp
    editor/markdowneditor.cpp
    editor/markdownhighlighter.cpp
    editor/textformatoverlaycontroller.cpp
    editor/breathmapwidget.cpp
    editor/textblockdata.h
    export/cmarkgfmexporter.cpp
    export/commandlineexporter.cpp
    export/exportdialog.cpp
    export/exporter.cpp
    export/exporterfactory.cpp
    export/exportformat.cpp
    markdown/cmarkgfmapi.cpp
    markdown/markdownast.cpp
    markdown/markdownnode.cpp
    prose/credentialstore.cpp
    prose/grammarsettingsdialog.cpp
    prose/performancepolicy.cpp
    prose/performancesettingsdialog.cpp
    prose/proseawarenesswidget.cpp
    prose/prosecontroller.cpp
    prose/profileeditordialog.cpp
    prose/providersettingsdialog.cpp
    prose/rewritereviewdialog.cpp
    prose/writerengineclient.cpp
    story/storyintelligencecontroller.cpp
    story/storyintelligencewidget.cpp
    settings/appsettings.cpp
    settings/localedialog.cpp
    settings/preferencesdialog.cpp
    settings/previewoptionsdialog.cpp
    settings/simplefontdialog.cpp
    spelling/spellcheckdecorator.cpp
    spelling/spellcheckdialog.cpp
    statistics/abstractstatisticswidget.cpp
    statistics/documentstatistics.cpp
    statistics/documentstatisticswidget.cpp
    statistics/sessionstatistics.cpp
    statistics/sessionstatisticswidget.cpp
    statistics/statisticsindicator.cpp
    theme/chromecolors.cpp
    theme/colorschemepreviewer.cpp
    theme/stylesheetbuilder.cpp
    theme/svgicontheme.cpp
    theme/theme.cpp
    theme/themeeditordialog.cpp
    theme/themerepository.cpp
    theme/themeselectiondialog.cpp
    appactions.cpp
    appmain.cpp
    bookmark.cpp
    documentmanager.cpp
    findreplace.cpp
    folderviewwidget.cpp
    library.cpp
    logging.cpp
    mainwindow.cpp
    messageboxhelper.cpp
    outlinewidget.cpp
    sidebar.cpp
    timelabel.cpp
    welcomedialog.cpp
    ${ghostwriter_QM_LOADER}
)

if(THOTHPAD_INSTRUMENTATION)
    target_sources(thothpad PRIVATE
        prose/proseinstrumentation.cpp
    )
    target_compile_definitions(thothpad PRIVATE THOTHPAD_INSTRUMENTATION)
endif()



find_package(Qt${QT_MAJOR_VERSION} ${QT_MIN_VERSION} REQUIRED COMPONENTS
    Concurrent
    Core
    Gui
    Svg
    Widgets
)

if(THOTHPAD_PACKAGE_VARIANT STREQUAL "Full")
    find_package(Qt${QT_MAJOR_VERSION} ${QT_MIN_VERSION} REQUIRED COMPONENTS
        WebChannel
        WebEngineWidgets
    )
    target_sources(thothpad PRIVATE
        preview/htmlpreview.cpp
        preview/previewproxy.cpp
        preview/sandboxedwebpage.cpp
    )
    target_compile_definitions(thothpad PRIVATE THOTHPAD_HAS_WEBENGINE)
else()
    target_sources(thothpad PRIVATE preview/htmlpreview_core.cpp)
endif()

if(WIN32)
    # Embeds resources/windows/thothpad.ico into thothpad.exe so Explorer,
    # Start Menu shortcuts, taskbar pins and Alt-Tab show the app icon
    # instead of the Qt fallback.
    target_sources(thothpad PRIVATE ../resources/windows/thothpad.rc)
endif()

find_package(
    KF${KF_MAJOR_VERSION} ${KF_MIN_VERSION}
    REQUIRED
    COMPONENTS
    ConfigWidgets
    CoreAddons
    Sonnet
    WidgetsAddons
    XmlGui
)

# For whatever reason, MSVC has issues statically linking with cmark-gfm.
if (MSVC)
    set(CMARK_LIBS
        libcmark-gfm
        libcmark-gfm-extensions
    )
else()
    set(CMARK_LIBS
        libcmark-gfm_static
        libcmark-gfm-extensions_static
    )
endif()

target_link_libraries(thothpad PRIVATE
    Qt::Concurrent
    Qt::Core
    Qt::Gui
    Qt::Svg
    Qt::Widgets
    KF${KF_MAJOR_VERSION}::ConfigCore
    KF${KF_MAJOR_VERSION}::ConfigWidgets
    KF${KF_MAJOR_VERSION}::CoreAddons
    KF${KF_MAJOR_VERSION}::SonnetCore
    KF${KF_MAJOR_VERSION}::SonnetUi
    KF${KF_MAJOR_VERSION}::XmlGui
    ${CMARK_LIBS}
)

if(THOTHPAD_PACKAGE_VARIANT STREQUAL "Full")
    target_link_libraries(thothpad PRIVATE
        Qt::WebChannel
        Qt::WebEngineWidgets
    )
endif()

# Ship Sonnet's hunspell client plugin and spell dictionaries next to the app
# so development builds spell-check like packaged releases. The Craft layout
# is Windows-specific: Linux/macOS dev setups provide these through system
# packages, so this stays a no-op there.
if(WIN32 AND TARGET KF${KF_MAJOR_VERSION}::SonnetCore)
    add_custom_command(TARGET thothpad POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
                "$<TARGET_FILE_DIR:KF${KF_MAJOR_VERSION}::SonnetCore>/../plugins/kf6/sonnet/sonnet_hunspell.dll"
                "$<TARGET_FILE_DIR:thothpad>/plugins/kf6/sonnet/sonnet_hunspell.dll"
        COMMAND ${CMAKE_COMMAND} -E copy_directory
                "$<TARGET_FILE_DIR:KF${KF_MAJOR_VERSION}::SonnetCore>/data/hunspell"
                "$<TARGET_FILE_DIR:thothpad>/data/hunspell"
    )
endif()

find_package(Qt6Keychain REQUIRED)
target_link_libraries(thothpad PRIVATE qt6keychain)
target_compile_definitions(thothpad PRIVATE THOTHPAD_HAS_QTKEYCHAIN)

set_target_properties(thothpad PROPERTIES
    WIN32_EXECUTABLE ON
    MACOSX_BUNDLE ON
)
