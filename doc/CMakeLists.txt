# SPDX-FileCopyrightText: 2022 Carl Schwan <carl@carlschwan.eu>
#
# SPDX-License-Identifier: GPL-3.0-or-later

if(NOT WIN32)
    kdoctools_create_manpage(man-thothpad.1.docbook 1 INSTALL_DESTINATION ${KDE_INSTALL_MANDIR})
endif()
