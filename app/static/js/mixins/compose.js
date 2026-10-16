/**
 * Combine component parts (mixins and the component's own members) into one
 * Alpine data object.
 *
 * Unlike object spread, this keeps getters (`get serverMode()`) as getters
 * instead of evaluating them once at spread time. Later parts win, so a
 * component overrides a mixin's method by defining its own.
 * @param {...object} parts
 * @returns {object}
 */
export function compose(...parts) {
    const target = {};
    for (const part of parts) {
        Object.defineProperties(target, Object.getOwnPropertyDescriptors(part));
    }
    return target;
}
