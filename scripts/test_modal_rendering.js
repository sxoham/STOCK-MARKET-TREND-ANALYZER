/**
 * Test suite for Trade Successful modal DOM construction and XSS safety.
 */
const assert = require('assert');

// Minimal DOM simulation for unit testing
class MockNode {
    constructor(nodeType, nodeName) {
        this.nodeType = nodeType;
        this.nodeName = nodeName;
    }
}

class MockText extends MockNode {
    constructor(data) {
        super(3, '#text');
        this.data = String(data);
    }
    get textContent() {
        return this.data;
    }
    set textContent(val) {
        this.data = String(val);
    }
}

class MockElement extends MockNode {
    constructor(tagName) {
        super(1, tagName.toUpperCase());
        this.tagName = tagName.toUpperCase();
        this.className = '';
        this.childNodes = [];
        this.classList = {
            classes: new Set(),
            add: (c) => this.classList.classes.add(c),
            remove: (c) => this.classList.classes.delete(c),
            contains: (c) => this.classList.classes.has(c)
        };
    }

    get textContent() {
        return this.childNodes.map(n => n.textContent).join('');
    }

    set textContent(val) {
        this.childNodes = [];
        if (val !== '' && val !== null && val !== undefined) {
            this.childNodes.push(new MockText(val));
        }
    }

    appendChild(child) {
        if (child instanceof MockDocumentFragment) {
            const added = [...child.childNodes];
            child.childNodes = [];
            for (const n of added) {
                this.childNodes.push(n);
            }
            return added[added.length - 1];
        }
        this.childNodes.push(child);
        return child;
    }

    append(...items) {
        for (const item of items) {
            if (item instanceof MockNode) {
                this.appendChild(item);
            } else {
                this.appendChild(new MockText(String(item)));
            }
        }
    }

    get innerHTML() {
        return this.childNodes.map(n => {
            if (n.nodeType === 3) {
                // Escape text for innerHTML representation
                return n.data.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            } else if (n.nodeType === 1) {
                const cls = n.className ? ` class="${n.className}"` : '';
                return `<${n.tagName.toLowerCase()}${cls}>${n.innerHTML}</${n.tagName.toLowerCase()}>`;
            }
            return '';
        }).join('');
    }

    querySelectorAll(tagName) {
        const results = [];
        for (const n of this.childNodes) {
            if (n.nodeType === 1) {
                if (n.tagName === tagName.toUpperCase()) results.push(n);
                results.push(...n.querySelectorAll(tagName));
            }
        }
        return results;
    }
}

class MockDocumentFragment extends MockNode {
    constructor() {
        super(11, '#document-fragment');
        this.childNodes = [];
    }

    appendChild(child) {
        this.childNodes.push(child);
        return child;
    }

    append(...items) {
        for (const item of items) {
            if (item instanceof MockNode) {
                this.appendChild(item);
            } else {
                this.appendChild(new MockText(String(item)));
            }
        }
    }

    get textContent() {
        return this.childNodes.map(n => n.textContent).join('');
    }
}

// Global DOM mocks
global.Node = MockNode;
global.document = {
    createElement: (tag) => new MockElement(tag),
    createTextNode: (text) => new MockText(text),
    createDocumentFragment: () => new MockDocumentFragment(),
    getElementById: (id) => elements[id] || null
};

// Elements registry
const elements = {
    messageModal: new MockElement('div'),
    msgModalTitle: new MockElement('h3'),
    msgModalContent: new MockElement('p')
};

// Mock formatWithCurrency
function formatWithCurrency(inrAmount) {
    return `₹${Number(inrAmount).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

// Target function under test (extracted from static/script.js)
function createTradeSuccessContent(action, qty, stock, price) {
    const fragment = document.createDocumentFragment();

    fragment.append(document.createTextNode(`${action} `));

    const qtySpan = document.createElement('span');
    qtySpan.className = 'highlight-text';
    qtySpan.textContent = typeof qty === 'number' ? qty.toLocaleString('en-IN') : String(qty);
    fragment.append(qtySpan);

    fragment.append(document.createTextNode(' shares of '));

    const stockNode = document.createTextNode(String(stock));
    fragment.append(stockNode);

    fragment.append(document.createTextNode(' at '));

    const priceSpan = document.createElement('span');
    priceSpan.className = 'highlight-text';
    priceSpan.textContent = formatWithCurrency(price);
    fragment.append(priceSpan);

    return fragment;
}

function showMessageModal(title, message, isError = false) {
    const modal = document.getElementById('messageModal');
    const titleEl = document.getElementById('msgModalTitle');
    const contentEl = document.getElementById('msgModalContent');

    if (!modal || !titleEl || !contentEl) return;

    titleEl.textContent = title;
    contentEl.textContent = '';

    if (message instanceof Node) {
        contentEl.appendChild(message);
    } else if (Array.isArray(message)) {
        contentEl.append(...message);
    } else {
        contentEl.textContent = message != null ? String(message) : '';
    }

    modal.classList.add('active');
}

// =============================================================================
// TEST SUITE
// =============================================================================

console.log("Running Trade Successful Modal Rendering Tests...\n");

// 1. BUY Success Test (Expected prompt example)
{
    const content = createTradeSuccessContent("Bought", 7, "RELIANCE.NS", 1302.50);
    showMessageModal("Trade Successful", content);

    const titleEl = elements.msgModalTitle;
    const contentEl = elements.msgModalContent;

    assert.strictEqual(titleEl.textContent, "Trade Successful");
    assert.strictEqual(contentEl.textContent, "Bought 7 shares of RELIANCE.NS at ₹1,302.50");

    // Check innerHTML has actual span tags, NOT escaped tags
    assert.strictEqual(
        contentEl.innerHTML,
        'Bought <span class="highlight-text">7</span> shares of RELIANCE.NS at <span class="highlight-text">₹1,302.50</span>'
    );

    // Verify spans have highlight-text class
    const spans = contentEl.querySelectorAll('span');
    assert.strictEqual(spans.length, 2);
    assert.strictEqual(spans[0].className, 'highlight-text');
    assert.strictEqual(spans[0].textContent, '7');
    assert.strictEqual(spans[1].className, 'highlight-text');
    assert.strictEqual(spans[1].textContent, '₹1,302.50');

    // Crucial: TextContent must NOT contain literal <span> text
    assert.ok(!contentEl.textContent.includes('<span'));
    assert.ok(!contentEl.textContent.includes('</span>'));

    console.log("✓ Test 1 Passed: BUY trade modal displays styled spans without literal markup.");
}

// 2. SELL Success Test
{
    const content = createTradeSuccessContent("Sold", 25, "TCS.NS", 3450.00);
    showMessageModal("Trade Successful", content);

    const contentEl = elements.msgModalContent;
    assert.strictEqual(contentEl.textContent, "Sold 25 shares of TCS.NS at ₹3,450.00");
    assert.strictEqual(
        contentEl.innerHTML,
        'Sold <span class="highlight-text">25</span> shares of TCS.NS at <span class="highlight-text">₹3,450.00</span>'
    );

    console.log("✓ Test 2 Passed: SELL trade modal functions identically with proper action verb.");
}

// 3. Indian Rupee / Multi-thousand quantity and price formatting
{
    const content = createTradeSuccessContent("Bought", 15000, "INFY.NS", 1875.25);
    showMessageModal("Trade Successful", content);

    const contentEl = elements.msgModalContent;
    assert.strictEqual(contentEl.textContent, "Bought 15,000 shares of INFY.NS at ₹1,875.25");
    assert.strictEqual(
        contentEl.innerHTML,
        'Bought <span class="highlight-text">15,000</span> shares of INFY.NS at <span class="highlight-text">₹1,875.25</span>'
    );

    console.log("✓ Test 3 Passed: Large quantities (15,000) and Rupee prices format correctly.");
}

// 4. XSS Injection Resistance Test on Ticker
{
    const maliciousTicker = "<script>alert('XSS')</script>";
    const content = createTradeSuccessContent("Bought", 10, maliciousTicker, 500);
    showMessageModal("Trade Successful", content);

    const contentEl = elements.msgModalContent;

    // Must NOT contain any script tag elements
    const scripts = contentEl.querySelectorAll('script');
    assert.strictEqual(scripts.length, 0, "No <script> elements should ever be created");

    // Raw script string must be treated purely as text
    assert.ok(contentEl.textContent.includes("<script>alert('XSS')</script>"));
    // InnerHTML must safely escape the angle brackets
    assert.ok(contentEl.innerHTML.includes('&lt;script&gt;alert(\'XSS\')&lt;/script&gt;'));

    console.log("✓ Test 4 Passed: Malicious ticker payload is safely sanitized as text (Zero XSS).");
}

// 5. XSS Injection Resistance Test on Image tag in Ticker
{
    const maliciousTicker2 = '<img src=x onerror=alert(1)>';
    const content = createTradeSuccessContent("Sold", 5, maliciousTicker2, 100);
    showMessageModal("Trade Successful", content);

    const contentEl = elements.msgModalContent;
    const imgs = contentEl.querySelectorAll('img');
    assert.strictEqual(imgs.length, 0, "No <img> elements should ever be created");
    assert.ok(contentEl.innerHTML.includes('&lt;img src=x onerror=alert(1)&gt;'));

    console.log("✓ Test 5 Passed: Malicious image injection in ticker is strictly inert.");
}

// 6. Compatibility with Plain String Callers
{
    // Ensure all other callers passing plain strings ("Insufficient funds", etc.) continue to work
    showMessageModal("Trade Failed", "Insufficient funds", true);
    assert.strictEqual(elements.msgModalTitle.textContent, "Trade Failed");
    assert.strictEqual(elements.msgModalContent.textContent, "Insufficient funds");
    assert.strictEqual(elements.msgModalContent.innerHTML, "Insufficient funds");

    showMessageModal("Invalid Input", "Please enter a valid quantity greater than 0", true);
    assert.strictEqual(elements.msgModalContent.textContent, "Please enter a valid quantity greater than 0");

    console.log("✓ Test 6 Passed: Plain string callers are preserved safely without regression.");
}

// 7. Plain string caller with HTML characters
{
    // A string containing angle brackets passed to showMessageModal must remain plain text
    showMessageModal("Notice", "Value must be < 100 & > 0");
    assert.strictEqual(elements.msgModalContent.textContent, "Value must be < 100 & > 0");
    assert.strictEqual(elements.msgModalContent.innerHTML, "Value must be &lt; 100 &amp; &gt; 0");

    console.log("✓ Test 7 Passed: String callers with angle brackets remain strict plain text.");
}

console.log("\nALL 7 MODAL RENDERING & XSS TESTS PASSED SUCCESSFULLY!");
