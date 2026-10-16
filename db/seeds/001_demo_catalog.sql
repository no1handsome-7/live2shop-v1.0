BEGIN;

INSERT INTO items(
    item_id,
    title,
    category_path,
    brand,
    style_tags,
    attributes,
    price_cent,
    currency,
    inventory,
    popularity_score,
    status
)
VALUES
(
    1001,
    'Levi''s 复古水洗牛仔夹克',
    ARRAY['服饰','男装','夹克'],
    'Levi''s',
    ARRAY['vintage','denim','workwear','复古','牛仔'],
    '{"color":"蓝色","material":"denim"}'::jsonb,
    49900,
    'CNY',
    100,
    0.85,
    'ACTIVE'
),
(
    1002,
    'UNIQLO 灯芯绒休闲衬衫',
    ARRAY['服饰','男装','衬衫'],
    'UNIQLO',
    ARRAY['casual','corduroy','休闲','灯芯绒'],
    '{"color":"棕色","material":"corduroy"}'::jsonb,
    29900,
    'CNY',
    100,
    0.70,
    'ACTIVE'
),
(
    1003,
    'Nike Dunk Low 低帮运动鞋',
    ARRAY['鞋靴','运动鞋','板鞋'],
    'Nike',
    ARRAY['streetwear','sneaker','街头'],
    '{"color":"白色","material":"leather"}'::jsonb,
    69900,
    'CNY',
    100,
    0.80,
    'ACTIVE'
)
ON CONFLICT (item_id) DO UPDATE SET
    title = EXCLUDED.title,
    category_path = EXCLUDED.category_path,
    brand = EXCLUDED.brand,
    style_tags = EXCLUDED.style_tags,
    attributes = EXCLUDED.attributes,
    price_cent = EXCLUDED.price_cent,
    currency = EXCLUDED.currency,
    inventory = EXCLUDED.inventory,
    popularity_score = EXCLUDED.popularity_score,
    status = EXCLUDED.status,
    updated_at = now();

INSERT INTO room_items(room_id, item_id, sort_order)
VALUES
    (20001, 1001, 1),
    (20001, 1002, 2),
    (20001, 1003, 3)
ON CONFLICT (room_id, item_id) DO UPDATE SET
    sort_order = EXCLUDED.sort_order;

SELECT setval(
    pg_get_serial_sequence('items', 'item_id'),
    GREATEST((SELECT MAX(item_id) FROM items), 1),
    true
);

COMMIT;
