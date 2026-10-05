-- Allow 'amendment_approval' gate type for document amendment workflow
alter table approval_gates
  drop constraint if exists approval_gates_gate_type_check,
  add constraint approval_gates_gate_type_check
    check (gate_type in ('intake_approval','document_release','amendment_approval'));
