bl_info = {
    "name": "MPFB ComfyUI Bridge",
    "author": "OpenAI",
    "version": (0, 1, 0),
    "blender": (4, 5, 0),
    "location": "View3D > Sidebar > MPFB > Operations > OpenPose",
    "description": "Send MPFB OpenPose, beauty renders and depth renders to ComfyUI.",
    "category": "MakeHuman",
}


from . import ui


def register():
    ui.register()


def unregister():
    ui.unregister()
