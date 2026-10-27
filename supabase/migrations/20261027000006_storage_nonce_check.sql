-- Fix: the upload policy read capture_nonces as the uploading user, but capture_nonces has RLS
-- with no select policy (nonces are never readable by clients), so every upload was refused
-- with "new row violates row-level security policy". The check now runs in a security definer
-- function that sees the nonce, and the policy only asks it yes or no.

create or replace function public.can_upload_capture(p_bucket text, p_name text)
returns boolean language sql stable security definer set search_path = public as $$
  select p_bucket in ('raw', 'sandbox') and exists (
    select 1 from capture_nonces n join devices d on d.id = n.device_id
     where n.device_id = auth.uid() and n.used_at is null and n.expires_at > now()
       and p_bucket = case when d.is_sandbox then 'sandbox' else 'raw' end
       and p_name = case when d.is_sandbox
             then d.sandbox_session_id || '/' || d.id || '/' || n.nonce || '.jpg'
             else d.id || '/' || n.nonce || '.jpg' end)
$$;

revoke execute on function public.can_upload_capture(text, text) from public, anon;
grant execute on function public.can_upload_capture(text, text) to authenticated;

do $$
begin
  if to_regclass('storage.objects') is null then
    raise notice 'storage schema not found, skipping upload policy';
    return;
  end if;
  execute 'drop policy if exists device_upload_with_nonce on storage.objects';
  execute $p$
    create policy device_upload_with_nonce on storage.objects for insert to authenticated
    with check (public.can_upload_capture(bucket_id, name))
  $p$;
end $$;
