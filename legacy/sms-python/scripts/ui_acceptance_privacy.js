async (page) => {
  const checks = [];
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const ensure = (value, label) => { if (!value) throw new Error(label); checks.push(label); };
  await page.context().clearCookies();
  await page.reload();
  await page.getByRole('textbox', {name: 'Operator password'}).fill('Synthetic QA password 123!');
  await page.getByRole('button', {name: 'Sign in', exact: true}).click();
  await page.getByRole('button', {name: 'Refresh workspace'}).waitFor({state: 'visible'});
  await page.locator('#activity-details > summary').click();
  ensure(!/\bSM[0-9a-fA-F]{32}\b/.test(await page.locator('#activity-items').innerText()), 'Privacy mode masks provider source identifiers in audit text');
  await page.locator('label.privacy-toggle').click();
  ensure(!(await page.getByRole('checkbox', {name: 'Showcase privacy'}).isChecked()), 'Visible privacy switch turns off');
  ensure(/\bSM[0-9a-fA-F]{32}\b/.test(await page.locator('#activity-items').innerText()), 'Authorized operator can reveal source identifiers when privacy is off');
  await page.locator('label.privacy-toggle').click();
  ensure(await page.getByRole('checkbox', {name: 'Showcase privacy'}).isChecked(), 'Visible privacy switch turns on');
  await page.getByRole('button', {name: 'Business profile', exact: true}).click();
  ensure((await page.locator('#business-form .form-actions').innerText()).includes('approved answers for this service'), 'Service profile hint describes the actual mode');
  await page.getByRole('button', {name: /Inbox/}).click();
  await page.getByRole('button', {name: 'Open conversation with Synthetic Insurance'}).click();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({path: 'D:/SMS Reception Assistant/output/playwright/10-final-reviewed-stop.png', fullPage: true});
  const snapshot = await page.evaluate(async () => (await fetch('/api/state')).json());
  ensure(snapshot.runtime.real_outbound === false && snapshot.outbox.items.every(item => !item.provider_sid), 'No UI test has submitted a provider message');
  ensure(errors.length === 0, 'Final page has no JavaScript runtime errors');
  await page.getByRole('button', {name: 'Sign out', exact: true}).click();
  await page.getByRole('textbox', {name: 'Operator password'}).waitFor({state: 'visible'});
  return {phase: 4, passed: checks.length, checks, errors, real_sending_enabled: false,
    state_counts: {appointments: snapshot.appointments.length, contacts: snapshot.contacts.length, outbox: snapshot.outbox.items.length}};
}
