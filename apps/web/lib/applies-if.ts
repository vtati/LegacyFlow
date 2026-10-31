/**
 * Client-side port of the pack ``applies_if`` applicability engine
 * (server source of truth: services/api/app/domains/intake/question_sets.py).
 *
 * The API's progress math, the interview's gap selection, and the
 * documents completeness gate all partition questions into
 * applicable / pending / pruned from these expressions. The intake
 * wizard needs the SAME partition to decide what to render: a question
 * whose gate has been answered decisively OFF is pruned — the server
 * excludes it from every denominator — so displaying it as a required
 * empty field (the 2026-10-31 defect: "Describe each specific gift… *"
 * shown under a section marked complete after the family answered No)
 * contradicts the state the rest of the product reports.
 *
 * Semantics mirror the Python evaluator exactly:
 *   True  — the rule is decisively satisfied (question applies)
 *   False — the rule is decisively violated (question is PRUNED)
 *   null  — a referenced gate key is unanswered (question is PENDING:
 *           still counted, still shown, never pruned)
 *
 * Grammar (no eval): or / and / not, == / != comparisons, operands are
 * question keys, true/false, numbers, or quoted strings; parentheses.
 */

export type AnswerMapLike = Record<string, unknown>;

const UNKNOWN: unique symbol = Symbol('applies-if-unknown');

type Token = { kind: 'op' | 'bool' | 'number' | 'string' | 'word'; text: string };

const TOKEN_RE =
  /\s+|(==|!=|\(|\))|\b(true|false)\b|(-?\d+(?:\.\d+)?)|('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")|([A-Za-z_][A-Za-z0-9_]*)/y;

function tokenize(expression: string): Token[] {
  const tokens: Token[] = [];
  let pos = 0;
  while (pos < expression.length) {
    TOKEN_RE.lastIndex = pos;
    const match = TOKEN_RE.exec(expression);
    if (!match || match.index !== pos || TOKEN_RE.lastIndex === pos) {
      throw new Error(
        `Cannot tokenize applies_if expression near: ${expression.slice(pos, pos + 20)}`,
      );
    }
    pos = TOKEN_RE.lastIndex;
    if (match[1] !== undefined) tokens.push({ kind: 'op', text: match[1] });
    else if (match[2] !== undefined) tokens.push({ kind: 'bool', text: match[2] });
    else if (match[3] !== undefined) tokens.push({ kind: 'number', text: match[3] });
    else if (match[4] !== undefined) tokens.push({ kind: 'string', text: match[4] });
    else if (match[5] !== undefined) tokens.push({ kind: 'word', text: match[5] });
    // otherwise the match was whitespace — skip.
  }
  return tokens;
}

type Node =
  | { kind: 'lit'; value: unknown }
  | { kind: 'key'; name: string }
  | { kind: 'cmp'; op: '==' | '!='; left: Node; right: Node }
  | { kind: 'not'; inner: Node }
  | { kind: 'and'; left: Node; right: Node }
  | { kind: 'or'; left: Node; right: Node };

const KEYWORDS = new Set(['and', 'or', 'not']);

class Parser {
  private pos = 0;

  constructor(private readonly tokens: Token[]) {}

  parse(): Node {
    const node = this.parseOr();
    if (this.pos !== this.tokens.length) {
      throw new Error('Unexpected trailing tokens in applies_if expression.');
    }
    return node;
  }

  private peek(): Token | null {
    return this.tokens[this.pos] ?? null;
  }

  private next(): Token {
    const token = this.peek();
    if (!token) throw new Error('Unexpected end of applies_if expression.');
    this.pos += 1;
    return token;
  }

  private parseOr(): Node {
    let node = this.parseAnd();
    while (this.peek()?.kind === 'word' && this.peek()?.text === 'or') {
      this.next();
      node = { kind: 'or', left: node, right: this.parseAnd() };
    }
    return node;
  }

  private parseAnd(): Node {
    let node = this.parseNot();
    while (this.peek()?.kind === 'word' && this.peek()?.text === 'and') {
      this.next();
      node = { kind: 'and', left: node, right: this.parseNot() };
    }
    return node;
  }

  private parseNot(): Node {
    if (this.peek()?.kind === 'word' && this.peek()?.text === 'not') {
      this.next();
      return { kind: 'not', inner: this.parseNot() };
    }
    return this.parseComparison();
  }

  private parseComparison(): Node {
    const left = this.parseOperand();
    const peek = this.peek();
    if (peek && peek.kind === 'op' && (peek.text === '==' || peek.text === '!=')) {
      this.next();
      return { kind: 'cmp', op: peek.text, left, right: this.parseOperand() };
    }
    return left;
  }

  private parseOperand(): Node {
    const token = this.next();
    if (token.kind === 'op' && token.text === '(') {
      const node = this.parseOr();
      const close = this.next();
      if (close.kind !== 'op' || close.text !== ')') {
        throw new Error('Missing closing parenthesis in applies_if expression.');
      }
      return node;
    }
    if (token.kind === 'bool') return { kind: 'lit', value: token.text === 'true' };
    if (token.kind === 'number') {
      return { kind: 'lit', value: token.text.includes('.') ? parseFloat(token.text) : parseInt(token.text, 10) };
    }
    if (token.kind === 'string') {
      return { kind: 'lit', value: token.text.slice(1, -1).replace(/\\(.)/g, '$1') };
    }
    if (token.kind === 'word') {
      if (KEYWORDS.has(token.text)) {
        throw new Error(`Unexpected keyword ${token.text} in applies_if expression.`);
      }
      if (['True', 'False', 'TRUE', 'FALSE'].includes(token.text)) {
        throw new Error(`Boolean literals in applies_if are lowercase: ${token.text}.`);
      }
      return { kind: 'key', name: token.text };
    }
    throw new Error(`Unexpected token ${token.text} in applies_if expression.`);
  }
}

/** Mirror of the server's ``is_answered``: False and 0 are answers;
 * null, "", [] and {} are not. */
export function isAnsweredValue(value: unknown): boolean {
  if (value === null || value === undefined) return false;
  if (typeof value === 'boolean') return true;
  if (typeof value === 'string' || Array.isArray(value)) return value.length > 0;
  if (typeof value === 'object') return Object.keys(value).length > 0;
  return true;
}

function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((item, i) => deepEqual(item, b[i]));
  }
  if (a && b && typeof a === 'object' && typeof b === 'object') {
    const aKeys = Object.keys(a);
    const bKeys = Object.keys(b as object);
    return (
      aKeys.length === bKeys.length &&
      aKeys.every((k) =>
        deepEqual((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]),
      )
    );
  }
  return false;
}

function evalRaw(node: Node, answers: AnswerMapLike): unknown {
  if (node.kind === 'lit') return node.value;
  if (node.kind === 'key') {
    const value = answers[node.name];
    return isAnsweredValue(value) ? value : UNKNOWN;
  }
  const result = evalBool(node, answers);
  return result === null ? UNKNOWN : result;
}

function truthOf(value: unknown): boolean | null {
  if (value === UNKNOWN) return null;
  if (typeof value === 'boolean') return value;
  if (value === null || value === undefined) return null;
  return Boolean(value);
}

function evalBool(node: Node, answers: AnswerMapLike): boolean | null {
  switch (node.kind) {
    case 'lit':
    case 'key':
      return truthOf(evalRaw(node, answers));
    case 'cmp': {
      const left = evalRaw(node.left, answers);
      const right = evalRaw(node.right, answers);
      if (left === UNKNOWN || right === UNKNOWN) return null;
      // Type-strict across the bool/number family, mirroring the server:
      // Python's True == 1 conflation must not make `count == true` decisive.
      const leftBoolNum = typeof left === 'boolean' || typeof left === 'number';
      const rightBoolNum = typeof right === 'boolean' || typeof right === 'number';
      if ((typeof left === 'boolean') !== (typeof right === 'boolean') && leftBoolNum && rightBoolNum) {
        return node.op === '!=';
      }
      const equal = deepEqual(left, right);
      return node.op === '==' ? equal : !equal;
    }
    case 'not': {
      const inner = evalBool(node.inner, answers);
      return inner === null ? null : !inner;
    }
    case 'and': {
      const left = evalBool(node.left, answers);
      const right = evalBool(node.right, answers);
      if (left === false || right === false) return false;
      if (left === null || right === null) return null;
      return true;
    }
    case 'or': {
      const left = evalBool(node.left, answers);
      const right = evalBool(node.right, answers);
      if (left === true || right === true) return true;
      if (left === null || right === null) return null;
      return false;
    }
  }
}

/**
 * Evaluate an ``applies_if`` expression against answers.
 * Returns true/false when decisive, null when a referenced gate key is
 * unanswered and the outcome is not already decided (Kleene logic).
 * Throws on a malformed expression, like the server evaluator.
 */
export function evaluateAppliesIf(expression: string, answers: AnswerMapLike): boolean | null {
  const tokens = tokenize(expression);
  if (tokens.length === 0) throw new Error('Empty applies_if expression.');
  return evalBool(new Parser(tokens).parse(), answers);
}

export type Applicability = 'applicable' | 'pending' | 'pruned';

/** One ``applies_if`` slot: absent/blank -> applicable (true). A
 * malformed expression fails OPEN for display (visible): the server
 * remains the authority for progress and gates, and hiding a question
 * because of a pack typo would strand the family. */
function appliesState(expr: string | null | undefined, answers: AnswerMapLike): boolean | null {
  if (expr === null || expr === undefined) return true;
  if (!expr.trim()) return true;
  try {
    return evaluateAppliesIf(expr, answers);
  } catch {
    return true;
  }
}

interface AppliesCarrier {
  applies_if?: string | null;
}

/** Combine a section's and a question's gate, exactly like the
 * server's partition: any false -> pruned; else any null -> pending. */
export function questionApplicability(
  section: AppliesCarrier,
  question: AppliesCarrier,
  answers: AnswerMapLike,
): Applicability {
  const states = [
    appliesState(section.applies_if, answers),
    appliesState(question.applies_if, answers),
  ];
  if (states.includes(false)) return 'pruned';
  if (states.includes(null)) return 'pending';
  return 'applicable';
}

export function visibleQuestions<T extends AppliesCarrier>(
  section: AppliesCarrier & { questions: T[] },
  answers: AnswerMapLike,
): T[] {
  return section.questions.filter(
    (question) => questionApplicability(section, question, answers) !== 'pruned',
  );
}

/** A section is pruned when EVERY question in it is pruned (the
 * server pre-registers such sections with zeroed progress). */
export function isSectionPruned(
  section: AppliesCarrier & { questions: AppliesCarrier[] },
  answers: AnswerMapLike,
): boolean {
  if (section.questions.length === 0) return false;
  return section.questions.every(
    (question) => questionApplicability(section, question, answers) === 'pruned',
  );
}
