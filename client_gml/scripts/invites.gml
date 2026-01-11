/// @description Invite handling with 5-minute expiration

function rcord_invites_prune() {
    var now = date_current_datetime();
    var remaining = ds_list_create();

    for (var i = 0; i < ds_list_size(global.rcord.invites); i++) {
        var invite = global.rcord.invites[| i];
        var invited_at = date_parse_datetime(invite.invited_at);
        var seconds = date_second_span(invited_at, now);
        if (seconds <= 300) {
            ds_list_add(remaining, invite);
        }
    }

    ds_list_clear(global.rcord.invites);
    for (var j = 0; j < ds_list_size(remaining); j++) {
        ds_list_add(global.rcord.invites, remaining[| j]);
    }
    ds_list_destroy(remaining);
}

function rcord_invite_time_left(invite) {
    var now = date_current_datetime();
    var invited_at = date_parse_datetime(invite.invited_at);
    var seconds = 300 - date_second_span(invited_at, now);
    return max(0, seconds);
}
