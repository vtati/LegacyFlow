import {
  Activity,
  Archive,
  BarChart3,
  BookOpen,
  Briefcase,
  Building2,
  ClipboardCheck,
  CreditCard,
  FileText,
  Flag,
  Gauge,
  HelpCircle,
  Home,
  Inbox,
  Landmark,
  LayoutDashboard,
  LayoutTemplate,
  LifeBuoy,
  Map,
  Package,
  PenLine,
  Settings,
  ShieldCheck,
  Upload,
  Users,
  Wallet,
  type LucideIcon,
} from 'lucide-react';

export interface NavItem {
  /** URL slug under the section, e.g. 'my-plan'. */
  slug: string;
  label: string;
  href: string;
  icon: LucideIcon;
  /** One-line placeholder description — generic, no legal content. */
  blurb: string;
}

export type AppSection = 'family' | 'firm' | 'admin' | 'attorney';

/** Family navigation (§5.5). */
export const FAMILY_NAV: NavItem[] = [
  {
    slug: 'home',
    label: 'Home',
    href: '/family/home',
    icon: Home,
    blurb: 'Your starting point and next best step.',
  },
  {
    slug: 'my-plan',
    label: 'My Plan',
    href: '/family/my-plan',
    icon: LayoutDashboard,
    blurb: 'Plan modules and implementation status.',
  },
  {
    slug: 'family-guardians',
    label: 'Family & Guardians',
    href: '/family/family-guardians',
    icon: Users,
    blurb: 'Household members and guardian choices.',
  },
  {
    slug: 'assets',
    label: 'Assets',
    href: '/family/assets',
    icon: Wallet,
    blurb: 'Your assets, their values, and beneficiaries.',
  },
  {
    slug: 'liabilities',
    label: 'Liabilities',
    href: '/family/liabilities',
    icon: CreditCard,
    blurb: 'Debts and recurring obligations.',
  },
  {
    slug: 'files',
    label: 'Files',
    href: '/family/files',
    icon: Upload,
    blurb: 'Upload and manage your plan documents.',
  },
  {
    slug: 'documents',
    label: 'Documents',
    href: '/family/documents',
    icon: FileText,
    blurb: 'Your plan documents and their status.',
  },
  {
    slug: 'signing',
    label: 'Signing',
    href: '/family/signing',
    icon: PenLine,
    blurb: 'Signing checklists and witness requirements.',
  },
  {
    slug: 'funding',
    label: 'Funding',
    href: '/family/funding',
    icon: Landmark,
    blurb: 'Funding tasks and evidence.',
  },
  {
    slug: 'vault',
    label: 'Vault',
    href: '/family/vault',
    icon: Archive,
    blurb: 'Final documents and family letter storage.',
  },
  {
    slug: 'checkup',
    label: 'Checkup',
    href: '/family/checkup',
    icon: ClipboardCheck,
    blurb: 'Annual review and life-event prompts.',
  },
  {
    slug: 'help',
    label: 'Help',
    href: '/family/help',
    icon: HelpCircle,
    blurb: 'Guidance and support for your plan.',
  },
];

/** Firm navigation (§5.5). */
export const FIRM_NAV: NavItem[] = [
  {
    slug: 'overview',
    label: 'Overview',
    href: '/firm/overview',
    icon: Gauge,
    blurb: 'Review queue, blockers, and recent activity.',
  },
  {
    slug: 'matters',
    label: 'Matters',
    href: '/firm/matters',
    icon: Briefcase,
    blurb: 'All client matters, filterable and paginated.',
  },
  {
    slug: 'review-queue',
    label: 'Review Queue',
    href: '/firm/review-queue',
    icon: Inbox,
    blurb: 'Packets awaiting attorney review.',
  },
  {
    slug: 'clients',
    label: 'Clients',
    href: '/firm/clients',
    icon: Users,
    blurb: 'Households and their members.',
  },
  {
    slug: 'signing',
    label: 'Signing',
    href: '/firm/signing',
    icon: PenLine,
    blurb: 'Client-reported execution and evidence.',
  },
  {
    slug: 'funding',
    label: 'Funding',
    href: '/firm/funding',
    icon: Landmark,
    blurb: 'Funding tasks, evidence, and escalations.',
  },
  {
    slug: 'templates',
    label: 'Templates',
    href: '/firm/templates',
    icon: LayoutTemplate,
    blurb: 'Template versions and approval state.',
  },
  {
    slug: 'knowledge-rules',
    label: 'Knowledge & Rules',
    href: '/firm/knowledge-rules',
    icon: BookOpen,
    blurb: 'Content packs, citations, and escalation rules.',
  },
  {
    slug: 'reports',
    label: 'Reports',
    href: '/firm/reports',
    icon: BarChart3,
    blurb: 'Operational metrics for the firm.',
  },
  {
    slug: 'team-settings',
    label: 'Team & Settings',
    href: '/firm/team-settings',
    icon: Settings,
    blurb: 'Roles, members, and firm configuration.',
  },
  {
    slug: 'payouts',
    label: 'Payouts',
    href: '/firm/payouts',
    icon: Wallet,
    blurb: 'Attorney payouts and case requests.',
  },
];

/** Platform admin navigation (§5.5). */
export const ADMIN_NAV: NavItem[] = [
  {
    slug: 'firms',
    label: 'Firms',
    href: '/admin/firms',
    icon: Building2,
    blurb: 'Tenant firms on the platform.',
  },
  {
    slug: 'jurisdictions',
    label: 'Jurisdictions',
    href: '/admin/jurisdictions',
    icon: Map,
    blurb: 'Supported jurisdictions and their status.',
  },
  {
    slug: 'content-packs',
    label: 'Content Packs',
    href: '/admin/content-packs',
    icon: Package,
    blurb: 'Pack drafts, review, and publication.',
  },
  {
    slug: 'templates',
    label: 'Templates',
    href: '/admin/templates',
    icon: LayoutTemplate,
    blurb: 'Platform template library.',
  },
  {
    slug: 'feature-flags',
    label: 'Feature Flags',
    href: '/admin/feature-flags',
    icon: Flag,
    blurb: 'Rollout controls for new capabilities.',
  },
  {
    slug: 'security-audit',
    label: 'Security & Audit',
    href: '/admin/security-audit',
    icon: ShieldCheck,
    blurb: 'Audit timelines, filters, and redaction.',
  },
  {
    slug: 'support',
    label: 'Support',
    href: '/admin/support',
    icon: LifeBuoy,
    blurb: 'Support requests and responses.',
  },
  {
    slug: 'system-health',
    label: 'System Health',
    href: '/admin/system-health',
    icon: Activity,
    blurb: 'Service status and background jobs.',
  },
];

/** Attorney navigation — casework, earnings, and availability. */
export const ATTORNEY_NAV: NavItem[] = [
  {
    slug: 'dashboard',
    label: 'Dashboard',
    href: '/attorney/dashboard',
    icon: LayoutDashboard,
    blurb: 'Your assigned cases and earnings.',
  },
  {
    slug: 'opportunities',
    label: 'Opportunities',
    href: '/attorney/opportunities',
    icon: Inbox,
    blurb: 'Unassigned cases you can request.',
  },
];

export const SECTION_NAV: Record<AppSection, NavItem[]> = {
  family: FAMILY_NAV,
  firm: FIRM_NAV,
  admin: ADMIN_NAV,
  attorney: ATTORNEY_NAV,
};

export const SECTION_TITLES: Record<AppSection, string> = {
  family: 'Family workspace',
  firm: 'Firm workspace',
  admin: 'Firm admin',
  attorney: 'Attorney workspace',
};

export function findNavItem(section: AppSection, slug: string): NavItem | undefined {
  return SECTION_NAV[section].find((item) => item.slug === slug);
}
