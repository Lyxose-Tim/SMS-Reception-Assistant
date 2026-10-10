async (page) => {
  const checks = [];
  const ensure = (value, label) => { if (!value) throw new Error(label); checks.push(label); };
  const state = () => page.evaluate(async () => (await fetch('/api/state')).json());
  const waitContact = (phone, predicate) => page.waitForFunction(async ({phone, predicate}) => {
    const snapshot = await (await fetch('/api/state')).json();
    const contact = snapshot.contacts.find(item => item.phone === phone);
    return predicate === 'reviewed' ? contact.reply_permission_basis === 'operator_review_human_only'
      : predicate === 'takeover' ? contact.handoff : !contact.handoff;
  }, {phone, predicate});
  const before = await state();
  ensure(before.runtime.real_outbound === false, 'Real sending remains disabled');
  ensure(before.runtime.recipient_policy === 'test_allowlist', 'Default controlled-test policy is visible');
  const insurance = '+12025550812';
  await page.getByRole('button', {name: 'Open conversation with Synthetic Insurance'}).click();
  const confirmation = page.getByRole('checkbox', {name: 'I reviewed this message as a service inquiry. My reply relates only to this inquiry.'});
  ensure(!(await confirmation.isChecked()), 'Unknown inquiry confirmation is not preselected');
  await page.getByRole('textbox', {name: 'Human team reply'}).fill('Synthetic owner reply: I will review your insurance question.');
  await page.getByRole('button', {name: 'Team reply', exact: true}).click();
  ensure((await state()).outbox.items.filter(item => item.phone === insurance).length === 0, 'Unconfirmed UI submit creates no queue item');
  await page.screenshot({path: 'D:/SMS Reception Assistant/output/playwright/01-unknown-review.png', fullPage: true});
  await confirmation.check();
  await page.getByRole('button', {name: 'Team reply', exact: true}).click();
  await waitContact(insurance, 'reviewed');
  const reviewed = await state();
  const contact = reviewed.contacts.find(item => item.phone === insurance);
  const queued = reviewed.outbox.items.find(item => item.phone === insurance);
  ensure(contact.handoff && contact.consent_status === 'none', 'Reviewed human reply preserves takeover and no marketing enrollment');
  ensure(queued.origin === 'human' && queued.permission_basis === 'operator_review_human_only', 'Human reply stores source and human-only permission');
  ensure(queued.permission_until === contact.reply_permission_until && queued.permission_source_event_id === contact.reply_permission_event_id, 'Queue permission is a fixed snapshot');
  await page.getByRole('button', {name: 'Refresh workspace'}).click();
  await page.screenshot({path: 'D:/SMS Reception Assistant/output/playwright/02-human-reply-queued.png', fullPage: true});
  await page.getByRole('button', {name: 'Resume assistant', exact: true}).click();
  await waitContact(insurance, 'resumed');
  ensure((await state()).contacts.find(item => item.phone === insurance).reply_permission_basis === 'operator_review_human_only', 'Resume does not upgrade human-only permission');
  await page.getByRole('button', {name: 'Take over', exact: true}).click();
  await waitContact(insurance, 'takeover');

  await page.getByRole('button', {name: 'Open conversation with Synthetic STOP'}).click();
  ensure(await page.getByRole('button', {name: 'Team reply', exact: true}).isDisabled(), 'STOP disables the human reply UI');
  await page.screenshot({path: 'D:/SMS Reception Assistant/output/playwright/03-stop-block.png', fullPage: true});
  await page.getByRole('button', {name: 'Business profile', exact: true}).click();
  for (const topic of ['hours', 'areas', 'services', 'pricing', 'requests']) {
    ensure(await page.locator('#faq-' + topic + '-approved').isChecked(), 'Approved FAQ editor: ' + topic);
  }
  ensure((await page.locator('#faq-requests-answer').inputValue()).includes('owner confirmation'), 'Fifth FAQ approved answer is editable');
  await page.locator('#faq-requests-answer').fill('UI approved request rules: every appointment request needs owner confirmation.');
  await page.getByRole('button', {name: 'Save business profile', exact: true}).click();
  await page.waitForFunction(async () => (await (await fetch('/api/state')).json()).business.approved_faqs.some(item => item.topic === 'requests' && item.answer.startsWith('UI approved')));
  await page.screenshot({path: 'D:/SMS Reception Assistant/output/playwright/04-five-approved-faqs.png', fullPage: true});
  await page.getByRole('button', {name: /Delivery queue/}).click();
  const pricing = page.locator('.outbox-card').filter({has: page.getByRole('button', {name: 'Synthetic Pricing', exact: true})});
  await pricing.getByRole('button', {name: 'Cancel queued message'}).click();
  await page.waitForFunction(async () => (await (await fetch('/api/state')).json()).outbox.items.some(item => item.phone === '+12025550818' && item.status === 'canceled'));
  ensure((await state()).outbox.items.some(item => item.phone === '+12025550818' && item.error_code === 'OPERATOR_CANCELED'), 'Queue cancellation is durable');
  await page.getByRole('button', {name: 'Pause sending', exact: true}).click();
  await page.waitForFunction(async () => (await (await fetch('/api/state')).json()).outbox.paused);
  ensure((await state()).outbox.paused, 'Queue pause is durable');
  await page.screenshot({path: 'D:/SMS Reception Assistant/output/playwright/05-queue-review.png', fullPage: true});
  await page.getByRole('button', {name: 'Resume sending', exact: true}).click();
  await page.waitForFunction(async () => !(await (await fetch('/api/state')).json()).outbox.paused);
  await page.locator('#activity-details > summary').click();
  ensure(await page.locator('#activity-items .activity-row').count() > 0, 'Recent audit activity is rendered');
  return {phase: 1, passed: checks.length, checks, real_sending_enabled: false};
}
