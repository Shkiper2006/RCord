/// @description UI state helpers for channel selection

function rcord_ui_select_channel(kind, name) {
    global.rcord.active_channel_type = kind;
    global.rcord.active_channel = name;
    rcord_request_list_messages(kind, name, 100);
}

function rcord_ui_show_channel_list() {
    global.rcord.active_channel_type = "";
    global.rcord.active_channel = noone;
}

function rcord_ui_channel_list_hidden() {
    return (global.rcord.active_channel != noone);
}
