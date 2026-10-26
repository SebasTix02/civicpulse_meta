-- Storage buckets and upload rules (API contract: uploadPhoto).
-- raw and sandbox are private; only the orchestrator writes public (anonymized copies).
-- Skipped on plain Postgres (CI), where the storage schema does not exist.

do $$
begin
  if to_regclass('storage.buckets') is null then
    raise notice 'storage schema not found, skipping buckets';
    return;
  end if;

  insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types) values
    ('raw', 'raw', false, 2621440, array['image/jpeg', 'image/webp']),
    ('sandbox', 'sandbox', false, 2621440, array['image/jpeg', 'image/webp']),
    ('public', 'public', true, 5242880, array['image/jpeg', 'image/png', 'application/json'])
  on conflict (id) do nothing;

  -- A device may upload only to the exact path its unused, unexpired nonce points to.
  execute $p$
    create policy device_upload_with_nonce on storage.objects for insert to authenticated
    with check (
      bucket_id in ('raw', 'sandbox')
      and exists (
        select 1 from public.capture_nonces n join public.devices d on d.id = n.device_id
         where n.device_id = auth.uid() and n.used_at is null and n.expires_at > now()
           and storage.objects.name = case when d.is_sandbox
                 then d.sandbox_session_id || '/' || d.id || '/' || n.nonce || '.jpg'
                 else d.id || '/' || n.nonce || '.jpg' end))
  $p$;
end $$;
