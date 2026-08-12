-- Generalize the first temperature-only action into a versioned sensor-read
-- contract. Temperature actions remain accepted so already-flashed v6.3
-- boards and queued work continue to interoperate during the transition.

alter table public.wearabllm_device_actions
    drop constraint if exists wearabllm_device_actions_action_type_check;
alter table public.wearabllm_device_actions
    add constraint wearabllm_device_actions_action_type_check
    check (action_type in ('expression', 'temperature_measurement', 'sensor_read'));

alter table public.wearabllm_device_actions
    drop constraint if exists wearabllm_device_actions_sensor_payload_check;
alter table public.wearabllm_device_actions
    add constraint wearabllm_device_actions_sensor_payload_check
    check (
        action_type <> 'sensor_read'
        or (
            payload ->> 'version' = '1'
            and payload ->> 'operation' = 'sensor_read'
            and jsonb_typeof(payload -> 'sensor_ids') = 'array'
            and jsonb_array_length(payload -> 'sensor_ids') between 1 and 16
            and payload ? 'schedule_id'
            and payload ? 'schedule_index'
            and payload ? 'schedule_count'
        )
    );

comment on column public.wearabllm_device_actions.payload is
    'Versioned device operation payload; sensor_read carries explicit sensor IDs and bounded schedule metadata.';
