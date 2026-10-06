-- Allow advance_directive and guardianship template types (correct Virginia classifications)
alter table document_templates
  drop constraint if exists document_templates_template_type_check,
  add constraint document_templates_template_type_check
    check (template_type in ('will','trust','poa','instruction_letter','codicil','trust_amendment','advance_directive','guardianship'));
