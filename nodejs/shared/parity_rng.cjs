'use strict';

/**
 * mulberry32 — 与 parity/context.py Mulberry32 同序列。
 * @param {number} seed
 * @returns {{ random: () => number, choice: (arr: any[]) => any, randInt: (min:number,max:number)=>number, generateRandomKey: (len?:number)=>Buffer }}
 */
function createParityRng(seed) {
    let a = (seed >>> 0);
    function random() {
        a = (a + 0x6D2B79F5) >>> 0;
        let t = a;
        t = Math.imul(t ^ (t >>> 15), t | 1);
        t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    }
    function choice(arr) {
        return arr[Math.floor(random() * arr.length)];
    }
    function randInt(min, max) {
        return min + Math.floor(random() * (max - min + 1));
    }
    function generateRandomKey(length = 16) {
        const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
        let out = '';
        for (let i = 0; i < length; i += 1) {
            out += chars.charAt(Math.floor(random() * chars.length));
        }
        return Buffer.from(out, 'utf8');
    }
    return { random, choice, randInt, generateRandomKey };
}

function installGlobalParityRng(seed) {
    const rng = createParityRng(seed);
    global.__RUSHBUY_PARITY_RNG = rng;
    Math.random = rng.random;
    return rng;
}

module.exports = { createParityRng, installGlobalParityRng };
