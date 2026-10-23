// @vitest-environment happy-dom
import { afterEach, describe, expect, it } from 'vitest';
import { HOST_ID, mountShadow, type Mounted } from './mount';

let mounted: Mounted | null = null;
afterEach(() => {
  mounted?.unmount();
  mounted = null;
});

describe('mountShadow', () => {
  it('renders into an open shadow root with inline styles and nothing in document.head', async () => {
    const headBefore = document.head.innerHTML;
    mounted = mountShadow(<p class="probe">hello</p>);
    expect(mounted).not.toBeNull();
    expect(document.getElementById(HOST_ID)).toBe(mounted!.host);
    const style = mounted!.root.querySelector('style');
    expect(style?.textContent).toContain(':host');
    expect(mounted!.root.querySelector('.probe')?.textContent).toBe('hello');
    expect(document.head.innerHTML).toBe(headBefore);
    expect(document.querySelector('.probe')).toBeNull();
  });

  it('refuses to mount twice', () => {
    mounted = mountShadow(<p>one</p>);
    expect(mountShadow(<p>two</p>)).toBeNull();
    expect(document.querySelectorAll(`#${HOST_ID}`)).toHaveLength(1);
  });

  it('unmounts cleanly', () => {
    const m = mountShadow(<p>bye</p>)!;
    m.unmount();
    expect(document.getElementById(HOST_ID)).toBeNull();
  });
});
