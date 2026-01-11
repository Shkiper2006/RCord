/// @description Theme palette for RCord (Discord-like)
function rcord_theme_init() {
    global.rcord_theme = {
        bg: make_color_rgb(43, 45, 49),
        panel: make_color_rgb(30, 31, 34),
        panel_alt: make_color_rgb(35, 36, 40),
        border: make_color_rgb(59, 61, 69),
        text: make_color_rgb(242, 243, 245),
        muted: make_color_rgb(148, 155, 164),
        accent: make_color_rgb(88, 101, 242),
        success: make_color_rgb(35, 165, 90),
        offline: make_color_rgb(79, 84, 92)
    };
}

function rcord_theme_color(name) {
    if (!is_struct(global.rcord_theme)) {
        rcord_theme_init();
    }
    return global.rcord_theme[$ name];
}
