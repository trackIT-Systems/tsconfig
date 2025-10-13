export function parseTimeString(timeStr) {
    if (timeStr.includes('sunrise')) {
        return {
            reference: 'sunrise',
            sign: timeStr.includes('-') ? '-' : '+',
            offset: timeStr.replace('sunrise', '').replace('+', '').replace('-', '').trim()
        };
    } else if (timeStr.includes('sunset')) {
        return {
            reference: 'sunset',
            sign: timeStr.includes('-') ? '-' : '+',
            offset: timeStr.replace('sunset', '').replace('+', '').replace('-', '').trim()
        };
    } else if (timeStr.includes('dawn')) {
        return {
            reference: 'dawn',
            sign: timeStr.includes('-') ? '-' : '+',
            offset: timeStr.replace('dawn', '').replace('+', '').replace('-', '').trim()
        };
    } else if (timeStr.includes('dusk')) {
        return {
            reference: 'dusk',
            sign: timeStr.includes('-') ? '-' : '+',
            offset: timeStr.replace('dusk', '').replace('+', '').replace('-', '').trim()
        };
    } else if (timeStr.includes('noon')) {
        return {
            reference: 'noon',
            sign: timeStr.includes('-') ? '-' : '+',
            offset: timeStr.replace('noon', '').replace('+', '').replace('-', '').trim()
        };


    } else {
        return {
            reference: 'time',
            sign: '+',
            offset: timeStr
        };
    }
}

export function updateTimeString(entry, type) {
    const reference = entry[`${type}Reference`];
    const sign = entry[`${type}Sign`];
    const offset = entry[`${type}Offset`];
    
    if (reference === 'time') {
        entry[type] = offset;
    } else {
        entry[type] = `${reference}${sign}${offset}`;
    }
}

