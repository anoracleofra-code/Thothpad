# SPDX-License-Identifier: GPL-3.0-or-later
# The real engine client scrubs PATH. Keep test-peer DLLs beside its executable,
# isolated from the app, rather than weakening that environment boundary.
file(GET_RUNTIME_DEPENDENCIES
    EXECUTABLES "${FIXTURE}"
    DIRECTORIES "${QT_BIN}"
    RESOLVED_DEPENDENCIES_VAR fixture_dlls
    UNRESOLVED_DEPENDENCIES_VAR missing_dlls
    PRE_EXCLUDE_REGEXES "api-ms-.*" "ext-ms-.*"
    POST_EXCLUDE_REGEXES ".*[/\\][Ww][Ii][Nn][Dd][Oo][Ww][Ss][/\\].*")
if(missing_dlls)
    message(FATAL_ERROR "Test peer has unresolved runtime dependencies: ${missing_dlls}")
endif()
get_filename_component(fixture_dir "${FIXTURE}" DIRECTORY)
foreach(dll IN LISTS fixture_dlls)
    file(COPY "${dll}" DESTINATION "${fixture_dir}")
endforeach()
