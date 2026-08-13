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
Fitting-loss coefficients for the smacna library.

Each function receives the same context dict as the shape generators in
junctions.py (see Junction.execute), augmented by the airflow solver with a
"flow_rate_lps"/"velocity_ms"/"reynolds" set of keys on every entry of
"connected_ports", plus "air_density"/"air_kinematic_viscosity". It returns
either a dict {edge_key: K} of per-port coefficients (each already referenced
to that port's own velocity -- required for converging/merging junctions
where each inlet leg has a physically distinct loss), a single float K
applied uniformly to every outlet port, or None to fall back to the solver's
generic default coefficient. Whatever is returned, these functions must not
do any pressure-unit arithmetic themselves -- that's the solver's job.

Elbow/transition/tee/wye losses are computed from real SMACNA/ASHRAE duct
fitting tables via HVACLibraryAPI.elbow_loss/transition_loss/branch_loss
(see library/smacna_loss.py for the table data and its sourcing/accuracy
caveats). Cross and multiport fittings use HVACLibraryAPI.manifold_loss,
which decomposes the junction into a sequence of the same tee/wye table
lookups (no dedicated SMACNA table exists for 4+ port fittings) -- see its
docstring for the single-trunk assumption and what falls back to the
original flat placeholder (a "true cross" with more than one inlet AND more
than one outlet, which has no single trunk to decompose against).
"""


def loss_through_generic(context):
    # No "through_generic" type is currently wired up in this library
    # (unlike builtin_basic), but kept for parity/reuse if one is added.
    api = context["hvac_api"]
    result = api.elbow_loss(context)
    if result is not None:
        return result
    return api.transition_loss(context)


def loss_elbow_generic(context):
    return context["hvac_api"].elbow_loss(context)


def loss_transition_generic(context):
    return context["hvac_api"].transition_loss(context)


def loss_tee_generic(context):
    return context["hvac_api"].branch_loss(context)


def loss_wye_generic(context):
    return context["hvac_api"].branch_loss(context)


def loss_cross_generic(context):
    result = context["hvac_api"].manifold_loss(context)
    return result if result is not None else 0.75


def loss_multiport_generic(context):
    result = context["hvac_api"].manifold_loss(context)
    return result if result is not None else 1.0
