BEGIN;

INSERT INTO users(user_id, username)
VALUES (10001, 'demo-user')
ON CONFLICT (user_id) DO NOTHING;

INSERT INTO anchors(anchor_id, display_name)
VALUES (50001, 'Vintage Demo Anchor')
ON CONFLICT (anchor_id) DO NOTHING;

INSERT INTO live_rooms(room_id, anchor_id, title, status)
VALUES (20001, 50001, 'Vintage Live Demo', 'OFFLINE')
ON CONFLICT (room_id) DO NOTHING;

COMMIT;
