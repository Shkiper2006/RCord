/// @description Networking protocol for RCord (newline-delimited JSON)
/// Call rcord_net_init() once and rcord_net_async(async_load) from Async - Networking.

function rcord_net_init() {
    if (!is_struct(global.rcord)) {
        rcord_state_init();
    }
}

function rcord_net_connect() {
    var settings = global.rcord.settings;
    if (global.rcord.connected) {
        return;
    }
    global.rcord.socket = network_connect(settings.host, settings.port);
}

function rcord_net_disconnect() {
    if (global.rcord.socket != -1) {
        network_disconnect(global.rcord.socket);
    }
    global.rcord.socket = -1;
    global.rcord.connected = false;
    global.rcord.buffer = "";
}

function rcord_net_send(action, payload) {
    if (!global.rcord.connected) {
        return;
    }
    var data = payload;
    data.action = action;
    var json = json_stringify(data) + "\n";
    var buffer = buffer_create(string_byte_length(json) + 1, buffer_fixed, 1);
    buffer_write(buffer, buffer_text, json);
    network_send_packet(global.rcord.socket, buffer, buffer_tell(buffer));
    buffer_delete(buffer);
}

function rcord_net_async(async_load) {
    var type = async_load[? "type"];
    if (type == network_type_connect) {
        global.rcord.connected = true;
        return;
    }
    if (type == network_type_disconnect) {
        rcord_net_disconnect();
        return;
    }
    if (type != network_type_data) {
        return;
    }

    var buffer = async_load[? "buffer"];
    var size = buffer_get_size(buffer);
    buffer_seek(buffer, buffer_seek_start, 0);
    var incoming = buffer_read(buffer, buffer_text);
    global.rcord.buffer += incoming;

    while (string_pos("\n", global.rcord.buffer) > 0) {
        var pos = string_pos("\n", global.rcord.buffer);
        var line = string_copy(global.rcord.buffer, 1, pos - 1);
        global.rcord.buffer = string_delete(global.rcord.buffer, 1, pos);
        if (string_length(line) == 0) {
            continue;
        }
        var message = json_parse(line);
        rcord_net_handle_message(message);
    }
}

function rcord_net_handle_message(message) {
    var action = message.action;
    if (action == "register_ok") {
        global.rcord.username = message.username;
        rcord_request_list_users();
        rcord_request_list_rooms();
        rcord_request_list_chats();
        rcord_request_list_invites();
    } else if (action == "login_ok") {
        global.rcord.username = message.username;
        rcord_request_list_users();
        rcord_request_list_rooms();
        rcord_request_list_chats();
        rcord_request_list_invites();
    } else if (action == "list_users") {
        ds_list_clear(global.rcord.users);
        var users = message.users;
        for (var i = 0; i < array_length(users); i++) {
            ds_list_add(global.rcord.users, users[i]);
        }
    } else if (action == "list_rooms") {
        ds_list_clear(global.rcord.rooms);
        var rooms = message.rooms;
        for (var i = 0; i < array_length(rooms); i++) {
            ds_list_add(global.rcord.rooms, rooms[i]);
        }
    } else if (action == "list_chats") {
        ds_list_clear(global.rcord.chats);
        var chats = message.chats;
        for (var i = 0; i < array_length(chats); i++) {
            ds_list_add(global.rcord.chats, chats[i]);
        }
    } else if (action == "list_invites") {
        ds_list_clear(global.rcord.invites);
        var invites = message.invites;
        for (var i = 0; i < array_length(invites); i++) {
            ds_list_add(global.rcord.invites, invites[i]);
        }
    } else if (action == "list_messages") {
        var target = message.target;
        var key = string(target);
        var list = ds_list_create();
        var msgs = message.messages;
        for (var i = 0; i < array_length(msgs); i++) {
            ds_list_add(list, msgs[i]);
        }
        ds_map_replace(global.rcord.messages, key, list);
    } else if (action == "send_message") {
        // Server confirms send; refresh list on current channel for consistency.
        if (global.rcord.active_channel != noone) {
            rcord_request_list_messages(global.rcord.active_channel_type, global.rcord.active_channel, 100);
        }
    }
}

function rcord_request_register(username, password) {
    rcord_net_send("register", { username: username, password: password });
}

function rcord_request_login(username, password) {
    rcord_net_send("login", { username: username, password: password });
}

function rcord_request_list_users() {
    rcord_net_send("list_users", {});
}

function rcord_request_list_rooms() {
    rcord_net_send("list_rooms", {});
}

function rcord_request_list_chats() {
    rcord_net_send("list_chats", {});
}

function rcord_request_list_invites() {
    rcord_net_send("list_invites", {});
}

function rcord_request_create_room(room) {
    rcord_net_send("create_room", { room: room });
}

function rcord_request_join_room(room) {
    rcord_net_send("join_room", { room: room });
}

function rcord_request_invite_room(room, username) {
    rcord_net_send("invite_room", { room: room, username: username });
}

function rcord_request_create_chat(username) {
    rcord_net_send("create_chat", { username: username });
}

function rcord_request_accept_chat(chat) {
    rcord_net_send("accept_chat", { chat: chat });
}

function rcord_request_send_message(target_kind, target_name, kind, text, filename, content) {
    var payload = { kind: kind };
    if (kind == "text") {
        payload.text = text;
    } else {
        payload.filename = filename;
        payload.content = content;
    }
    if (target_kind == "room") {
        payload.room = target_name;
    } else {
        payload.chat = target_name;
    }
    rcord_net_send("send_message", payload);
}

function rcord_request_list_messages(target_kind, target_name, limit) {
    var payload = { limit: limit };
    if (target_kind == "room") {
        payload.room = target_name;
    } else {
        payload.chat = target_name;
    }
    rcord_net_send("list_messages", payload);
}

function rcord_request_heartbeat() {
    rcord_net_send("heartbeat", {});
}

function rcord_request_logout() {
    rcord_net_send("logout", {});
}
