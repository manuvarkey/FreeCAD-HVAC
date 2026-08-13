# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the HVAC addon.

################################################################################
#                                                                              #
#   Copyright (c) 2026 Francisco Rosa                                          #
#                                                                              #
#   This addon is free software; you can redistribute it and/or modify it      #
#   under the terms of the GNU Lesser General Public License as published      #
#   by the Free Software Foundation; either version 2.1 of the License, or     #
#   (at your option) any later version.                                        #
#                                                                              #
#   This addon is distributed in the hope that it will be useful,              #
#   but WITHOUT ANY WARRANTY; without even the implied warranty of             #
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.                       #
#                                                                              #
#   See the GNU Lesser General Public License for more details.                #
#                                                                              #
#   You should have received a copy of the GNU Lesser General Public           #
#   License along with this addon. If not, see https://www.gnu.org/licenses    #
#                                                                              #
################################################################################

"""
Placeholder fitting-loss coefficients for the builtin_basic library.

Each function receives the same context dict as the shape generators in
junctions.py (see Junction.execute), augmented by the airflow solver with a
"flow_rate_lps"/"velocity_ms" pair on every entry of "connected_ports", plus
"air_density"/"air_kinematic_viscosity". It must return a single dimensionless
loss coefficient K (float) or None to fall back to the solver's generic
default. K is applied by the solver as K * velocity_pressure(outlet_port) --
these functions must not do any pressure-unit arithmetic themselves.

These values are generic order-of-magnitude placeholders, not SMACNA/ASHRAE
table lookups. They exist so the airflow solver produces complete, roughly
sane results end-to-end; replace with accurate per-geometry coefficient
tables as the library is developed further.
"""


def loss_through_generic(context):
    return 0.25


def loss_elbow_generic(context):
    return 0.25


def loss_transition_generic(context):
    return 0.15


def loss_tee_generic(context):
    return 0.75


def loss_wye_generic(context):
    return 0.4


def loss_cross_generic(context):
    return 0.75


def loss_multiport_generic(context):
    return 1.0
