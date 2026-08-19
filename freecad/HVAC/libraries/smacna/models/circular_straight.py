from operator import itemgetter

HVAC_PARTSCRIPT_API = 1


def generate(context):
    api, sp, ep, params = itemgetter("hvac_api", "start_point", "end_point", "params")(context)
    diameter = itemgetter("Diameter")(params)
    shape = api.make_straight_shape(
        start_point=sp,
        end_point=ep,
        profile="Circular",
        section_params={"Diameter": diameter},
        profile_x_axis=context.get("profile_x_axis"),
    )
    return {"shape": shape}
