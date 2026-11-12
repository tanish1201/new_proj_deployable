\set ON_ERROR_STOP on

BEGIN;

UPDATE users
SET email = 'demo.student@example.com'
WHERE id = 900001 AND email = 'demo.student@learnhub.local';

UPDATE users
SET email = 'demo.instructor@example.com'
WHERE id = 900002 AND email = 'demo.instructor@learnhub.local';

INSERT INTO users (id, email, password_hash, role, created_at)
VALUES
    (900001, 'demo.student@example.com', '$2b$12$nad749I9b5oG.xr1yXz0n.D8a0BgDZWcbNtnBgJTAvYJylcfi6Pbi', 'student', NOW()),
    (900002, 'demo.instructor@example.com', '$2b$12$nad749I9b5oG.xr1yXz0n.D8a0BgDZWcbNtnBgJTAvYJylcfi6Pbi', 'instructor', NOW())
ON CONFLICT (id) DO UPDATE
SET password_hash = EXCLUDED.password_hash,
    email = EXCLUDED.email,
    role = EXCLUDED.role;

SELECT setval(
    pg_get_serial_sequence('users', 'id'),
    GREATEST(COALESCE((SELECT MAX(id) FROM users), 1), 1),
    true
);

COMMIT;

\connect enrollments

BEGIN;

INSERT INTO enrollments (user_id, course_id, status, enrolled_at)
VALUES
    (900001, '650000000000000000000001', 'active', NOW() - INTERVAL '12 days'),
    (900001, '650000000000000000000002', 'active', NOW() - INTERVAL '5 days')
ON CONFLICT (user_id, course_id) DO NOTHING;

COMMIT;

\connect notifications

BEGIN;

INSERT INTO notification_log (event_id, user_id, type, channel, status, payload, created_at)
VALUES
    ('demo:user.registered:900001', '900001', 'user.registered', 'log', 'recorded',
     '{"user_id": 900001, "email": "demo.student@example.com"}'::jsonb, NOW() - INTERVAL '14 days'),
    ('demo:enrollment.created:900001:650000000000000000000001', '900001', 'enrollment.created', 'log', 'recorded',
     '{"user_id": 900001, "course_id": "650000000000000000000001"}'::jsonb, NOW() - INTERVAL '12 days'),
    ('demo:enrollment.created:900001:650000000000000000000002', '900001', 'enrollment.created', 'log', 'recorded',
     '{"user_id": 900001, "course_id": "650000000000000000000002"}'::jsonb, NOW() - INTERVAL '5 days'),
    ('demo:course.completed:900001:650000000000000000000001', '900001', 'course.completed', 'log', 'recorded',
     '{"user_id": 900001, "course_id": "650000000000000000000001"}'::jsonb, NOW() - INTERVAL '2 days')
ON CONFLICT (event_id) DO UPDATE
SET payload = EXCLUDED.payload;

COMMIT;
