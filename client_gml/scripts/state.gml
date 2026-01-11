/// @description Global state storage for RCord
function rcord_state_init() {
    global.rcord = {
        socket: -1,
        buffer: "",
        connected: false,
        username: "",
        active_channel: noone,
        active_channel_type: "",
        users: ds_list_create(),
        rooms: ds_list_create(),
        chats: ds_list_create(),
        invites: ds_list_create(),
        messages: ds_map_create(), // key: "room:<name>" or "chat:<name>"
        user_status: ds_map_create(),
        settings: rcord_settings_load()
    };
}

function rcord_state_clear_lists() {
    if (is_struct(global.rcord)) {
        ds_list_clear(global.rcord.users);
        ds_list_clear(global.rcord.rooms);
        ds_list_clear(global.rcord.chats);
        ds_list_clear(global.rcord.invites);
        ds_map_clear(global.rcord.messages);
        ds_map_clear(global.rcord.user_status);
    }
}

function rcord_state_message_key(kind, name) {
    return string(kind) + ":" + string(name);
}
