/// @description Load and save client settings (host/port/media)
function rcord_settings_default() {
    return {
        host: "127.0.0.1",
        port: 8765,
        media_port: 8766
    };
}

function rcord_settings_path() {
    return "settings.json";
}

function rcord_settings_load() {
    var path = rcord_settings_path();
    if (file_exists(path)) {
        var buf = buffer_load(path);
        var text = buffer_read(buf, buffer_text);
        buffer_delete(buf);
        var data = json_parse(text);
        return data;
    }
    return rcord_settings_default();
}

function rcord_settings_save(settings) {
    var json = json_stringify(settings);
    var buf = buffer_create(string_byte_length(json) + 1, buffer_fixed, 1);
    buffer_write(buf, buffer_text, json);
    buffer_save(buf, rcord_settings_path());
    buffer_delete(buf);
}
