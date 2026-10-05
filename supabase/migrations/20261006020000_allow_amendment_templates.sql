-- Allow codicil and trust_amendment template types for amendment workflow
alter table document_templates
  drop constraint if exists document_templates_template_type_check,
  add constraint document_templates_template_type_check
    check (template_type in ('will','trust','poa','instruction_letter','codicil','trust_amendment'));
