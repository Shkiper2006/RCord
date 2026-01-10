# Rooms layout notes

## rm_login
- Centered card for login/registration.
- Server settings form below (host/port/media).
- On register success -> navigate to `rm_main`.
- On login success -> `rcord_request_list_rooms`, `rcord_request_list_chats`,
  `rcord_request_list_users`, `rcord_request_list_invites`.

## rm_main
- Left panel width: 20%.
- Center panel width: 60%.
- Right panel width: 20%.
- Borders: 1px using `rcord_theme_color("border")`.
- Use `rcord_ui_channel_list_hidden()` to collapse channel list while inside a
  channel; show **Список каналов** button to reset.
