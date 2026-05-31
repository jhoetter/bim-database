import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const evidenceDir = path.resolve('../tmp/playwright-renderer');

test.beforeAll(() => {
  fs.mkdirSync(evidenceDir, { recursive: true });
});

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('bim-db:annotate:mcp-render', 'false');
    window.localStorage.setItem('bim-db:annotate:show-grid', 'false');
    window.localStorage.setItem('bim-db:annotate:img-opacity', '0.2');
  });
});

test('captures normal UI and Agent View renderer screenshots', async ({ page }) => {
  await page.goto('/house-22/scene/house-22-floorplan-eg.png/annotate');
  const agentViewButton = page.getByRole('button', { name: /Agentenansicht/ });
  await expect(agentViewButton).toBeVisible();
  await expect(page.getByText(/W.nde \(14\)/i)).toBeVisible();
  await page.screenshot({
    path: path.join(evidenceDir, 'house-22-eg-normal-ui.png'),
    fullPage: true,
  });

  const agentRender = page.waitForResponse(
    (response) => response.url().includes('/grid-with-labels') && response.ok(),
  );
  await agentViewButton.click();
  await agentRender;
  await expect(agentViewButton).toHaveAttribute('aria-pressed', 'true');
  await page.waitForLoadState('networkidle');
  await page.screenshot({
    path: path.join(evidenceDir, 'house-22-eg-agent-view.png'),
    fullPage: true,
  });
});
