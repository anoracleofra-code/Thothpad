# SPDX-FileCopyrightText: 2022-2024 Megan Conkle <megan.conkle@kdemail.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

cmake_minimum_required(VERSION 3.16)

project(unittest VERSION 1.0.0 LANGUAGES CXX)

function(thothpad_configure_test test_name)
    set_tests_properties(${test_name} PROPERTIES
        ENVIRONMENT "QT_QPA_PLATFORM=offscreen"
    )
    if (WIN32)
        get_target_property(qt_core_location Qt6::Core IMPORTED_LOCATION_RELEASE)
        if (NOT qt_core_location)
            get_target_property(qt_core_location Qt6::Core IMPORTED_LOCATION)
        endif()
        get_filename_component(qt_bin_dir "${qt_core_location}" DIRECTORY)
        get_filename_component(qt_prefix "${qt_bin_dir}" DIRECTORY)
        set_tests_properties(${test_name} PROPERTIES
            ENVIRONMENT_MODIFICATION
                "PATH=path_list_prepend:${qt_bin_dir};QT_PLUGIN_PATH=path_list_prepend:${qt_prefix}/plugins;QT_QPA_PLATFORM_PLUGIN_PATH=set:${qt_prefix}/plugins/platforms;QT_QPA_PLATFORM=set:offscreen"
        )
    endif()
endfunction()

add_subdirectory(asynctextwriter)
add_subdirectory(bookmark)
add_subdirectory(library)
add_subdirectory(textformatoverlaycontroller)
add_subdirectory(spellcheckdecorator)
add_subdirectory(rewritereviewdialog)
add_subdirectory(prosediagnostic)
add_subdirectory(proseawarenesswidget)
add_subdirectory(storyintelligencewidget)
add_subdirectory(proseoverlaydelta)
add_subdirectory(outlinewidget)
add_subdirectory(writerengineclient)
add_subdirectory(welcomedialog)
add_subdirectory(themerepository)
add_subdirectory(editorperformance)
add_subdirectory(efficiencycollector)

if (THOTHPAD_INSTRUMENTATION)
    add_subdirectory(prosinstrumentation)
endif()

enable_testing(true)
