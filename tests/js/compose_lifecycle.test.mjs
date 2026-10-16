import assert from 'node:assert/strict';
import { test } from 'node:test';
import { compose } from '../../app/static/js/mixins/compose.js';
import { lifecycleMixin } from '../../app/static/js/mixins/lifecycleMixin.js';
import { installBrowser } from './helpers.mjs';

test('compose keeps getters live instead of evaluating them once', () => {
    let state = 1;
    const mixin = {
        get value() {
            return state;
        },
    };
    const component = compose(mixin, { other: true });
    assert.equal(component.value, 1);
    state = 2;
    assert.equal(component.value, 2);
});

test('compose: later parts override earlier ones, including methods the mixin defines', () => {
    const component = compose({ greet: () => 'mixin', kept: 1 }, { greet: () => 'component' });
    assert.equal(component.greet(), 'component');
    assert.equal(component.kept, 1);
});

test('destroy() removes listeners and stops intervals', t => {
    const browser = installBrowser();
    t.after(browser.restore);
    t.mock.timers.enable({ apis: ['setInterval'] });

    const component = compose(lifecycleMixin(), {});
    let ticks = 0;
    component.every(1000, () => ticks++);
    component.listen(browser.window, 'ping', () => {});

    t.mock.timers.tick(3000);
    assert.equal(ticks, 3);
    assert.equal(browser.listenerCount('ping'), 1);

    component.destroy();
    t.mock.timers.tick(3000);
    assert.equal(ticks, 3, 'no more ticks after destroy');
    assert.equal(browser.listenerCount('ping'), 0);
});

test('nothing is registered after destroy (an init() that was still loading)', t => {
    const browser = installBrowser();
    t.after(browser.restore);
    t.mock.timers.enable({ apis: ['setInterval'] });

    const component = compose(lifecycleMixin(), {});
    component.destroy();
    let ticks = 0;
    component.every(1000, () => ticks++);
    component.listen(browser.window, 'ping', () => {});
    t.mock.timers.tick(5000);
    assert.equal(ticks, 0);
    assert.equal(browser.listenerCount('ping'), 0);
});

test('onDestroy runs the cleanup', t => {
    const browser = installBrowser();
    t.after(browser.restore);
    const component = compose(lifecycleMixin(), {});
    let cleaned = false;
    component.onDestroy(() => {
        cleaned = true;
    });
    component.destroy();
    assert.equal(cleaned, true);
});
