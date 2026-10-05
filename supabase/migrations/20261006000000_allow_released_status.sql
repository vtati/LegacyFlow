-- Allow 'released' status for production document packages
alter table document_packages
  drop constraint if exists document_packages_status_check,
  add constraint document_packages_status_check
    check (status in ('draft','demo_released','released'));
