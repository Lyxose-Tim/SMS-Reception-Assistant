async (page) => {
  const checks = [];
  const ensure = (value, label) => { if (!value) throw new Error(label); checks.push(label); };
  const snapshot = await page.evaluate(async () => (await fetch('/api/state')).json());
  const booking = snapshot.contacts.find(item => item.phone === '+12025550813').booking;
  ensure(snapshot.appointments.length === 1, 'Second request does not prematurely create another appointment');
  ensure(booking.service === 'Standard cleaning' && !booking.area && !booking.desired_time && !booking.contact, 'Second request starts without old area/time/contact');
  await page.getByRole('button', {name: /Inbox/}).click();
  await page.getByRole('button', {name: 'Refresh workspace'}).click();
  await page.getByRole('button', {name: 'Open conversation with Synthetic Booking'}).click();
  ensure((await page.locator('#message-list').innerText()).includes('Which area'), 'UI asks for the new location');
  await page.setViewportSize({width: 1440, height: 1050});
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({path: 'D:/SMS Reception Assistant/output/playwright/06-second-request-new-draft.png', fullPage: true});
  return {phase: 2, passed: checks.length, checks, real_sending_enabled: false};
}
