# library_api.py
class HVACLibraryAPI:
    API_VERSION = 1

    @staticmethod
    def make_profile_frame(direction, preferred_x=None, origin=None):
        from .hvaclib import make_profile_frame
        return make_profile_frame(direction, preferred_x, origin)
