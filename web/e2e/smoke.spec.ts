import { expect, test } from '@playwright/test'

test('renders the honest engineering baseline without horizontal overflow', async ({ page }) => {
  await page.goto('/home')
  await expect(page.getByRole('heading', { name: /把重点工作变成/ })).toBeVisible()
  await expect(page.getByText('不展示伪造生产数据')).toBeVisible()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
  expect(overflow).toBe(false)
})
