'use client';

import { firmApi, type MatterSummary } from '@/lib/api-client';

const PAGE_SIZE = 100;

/** Normalize a matter status for comparison — the API uses UPPER_SNAKE_CASE. */
export function normalizeStatus(status: string): string {
  return status.trim().toUpperCase();
}

/** Fetch every matter visible to the firm, following pagination. */
export async function fetchAllMatters(): Promise<MatterSummary[]> {
  const items: MatterSummary[] = [];
  let page = 1;
  for (;;) {
    const response = await firmApi.listMatters({ page, pageSize: PAGE_SIZE });
    items.push(...response.items);
    if (response.items.length < response.page_size || items.length >= response.total) break;
    page += 1;
  }
  return items;
}
