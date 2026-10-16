import assert from 'node:assert/strict';
import { test } from 'node:test';
import { formatApiError } from '../../app/static/js/utils/apiUtils.js';

test('formatApiError: the message, or the fallback if there is none', () => {
    assert.equal(formatApiError({ message: 'Invalid schedule' }, 'Failed'), 'Invalid schedule');
    assert.equal(formatApiError({}, 'Failed'), 'Failed');
    assert.equal(formatApiError('plain string detail', 'Failed'), 'Failed');
    assert.equal(formatApiError(undefined, 'Failed'), 'Failed');
});

test('formatApiError: lists the field errors', () => {
    assert.equal(
        formatApiError({ message: 'Invalid', errors: ['a is bad', 'b is bad'] }, 'Failed'),
        'Invalid\nValidation errors: a is bad, b is bad'
    );
});

test('formatApiError: pydantic validation errors with their location', () => {
    assert.equal(
        formatApiError({ message: 'Invalid', validation_errors: [{ loc: ['body', 'port'], msg: 'too big' }] }, 'Failed'),
        'Invalid\nValidation errors: body.port: too big'
    );
});
