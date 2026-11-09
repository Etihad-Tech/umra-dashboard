-- The dashboard's services table (flights, hotels, train, transfer per package) moved out of the public
-- data/services.js into the app_settings row 'package_services' (loaded by `python -m collector.import_services`).
-- Every signed-in, active user may read THAT row only; every other app_settings row (Telegram chat, ...)
-- stays admin-only. Writes stay admin-only (app_settings_admin_all) and the importer uses the service role.

create policy app_settings_select_services on public.app_settings
  for select to authenticated
  using (key = 'package_services' and (select app.is_active_user()));
