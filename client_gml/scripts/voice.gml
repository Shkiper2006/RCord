/// @description Voice chat stubs (device selection + activity flag)

function rcord_voice_init() {
    global.rcord_voice = {
        active: false,
        input_device: "default",
        speaking: false
    };
}

function rcord_voice_set_device(device_name) {
    if (!is_struct(global.rcord_voice)) {
        rcord_voice_init();
    }
    global.rcord_voice.input_device = device_name;
}

function rcord_voice_start() {
    if (!is_struct(global.rcord_voice)) {
        rcord_voice_init();
    }
    global.rcord_voice.active = true;
}

function rcord_voice_stop() {
    if (!is_struct(global.rcord_voice)) {
        rcord_voice_init();
    }
    global.rcord_voice.active = false;
    global.rcord_voice.speaking = false;
}

function rcord_voice_set_speaking(is_speaking) {
    if (!is_struct(global.rcord_voice)) {
        rcord_voice_init();
    }
    global.rcord_voice.speaking = is_speaking;
}
