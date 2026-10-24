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

  it('resets the host element with an inline !important all: initial so host-page div rules cannot reach it', () => {
    mounted = mountShadow(<p>isolated</p>);
    const host = mounted!.host;
    expect(host.style.getPropertyValue('all')).toBe('initial');
    expect(host.style.getPropertyPriority('all')).toBe('important');
  });

  it('uses .rw-body as the only scroll container and contains overscroll', () => {
    mounted = mountShadow(<p />);
    const css = mounted!.root.querySelector('style')!.textContent!;
    const rule = (selector: string) => {
      const start = css.indexOf(`\n${selector} {`);
      expect(start).toBeGreaterThanOrEqual(0);
      return css.slice(start, css.indexOf('}', start));
    };
    expect(rule('.rw-body')).toMatch(/overflow-y: auto/);
    expect(rule('.rw-body')).toMatch(/overscroll-behavior: contain/);
    expect(rule('.rw-messages')).not.toMatch(/overflow/);
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
