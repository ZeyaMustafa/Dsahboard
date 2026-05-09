#!/usr/bin/env python3
"""
Script to take screenshots of all dashboard pages for review.
"""

import os
import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

def setup_driver():
    """Setup Chrome driver with appropriate options."""
    chrome_options = Options()
    chrome_options.add_argument("--headless")  # Run in background
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-logging")
    chrome_options.add_argument("--log-level=3")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.set_page_load_timeout(30)
    return driver

def take_screenshot(driver, url, filename):
    """Take a screenshot of the given URL and save it to filename."""
    try:
        print(f"Loading {url}...")
        driver.get(url)
        
        # Wait for page to load
        time.sleep(3)
        
        # Wait for any Plotly charts to render (they might take a moment)
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "plotly"))
            )
        except:
            # If no plotly charts or timeout, just wait a bit more
            pass
        
        # Additional wait for charts to fully render
        time.sleep(5)
        
        # Take screenshot
        driver.save_screenshot(filename)
        print(f"Screenshot saved: {filename}")
        return True
    except Exception as e:
        print(f"Error taking screenshot of {url}: {e}")
        return False

def main():
    """Main function to take screenshots of all dashboard pages."""
    # Ensure screenshots directory exists
    screenshots_dir = "/tmp/dashboard_screenshots"
    os.makedirs(screenshots_dir, exist_ok=True)
    
    # Setup driver
    driver = setup_driver()
    
    try:
        # Define pages to screenshot
        base_url = "http://127.0.0.1:5000"
        pages = [
            ("/", "index"),
            ("/daily", "daily"), 
            ("/weekly", "weekly"),
            ("/monthly", "monthly"),
            ("/about", "about")
        ]
        
        success_count = 0
        
        for path, name in pages:
            url = f"{base_url}{path}"
            filename = os.path.join(screenshots_dir, f"screenshot_{name}.png")
            
            if take_screenshot(driver, url, filename):
                success_count += 1
            
            # Small delay between requests
            time.sleep(2)
        
        print(f"\nCompleted! {success_count}/{len(pages)} screenshots taken successfully.")
        print(f"Screenshots saved in: {screenshots_dir}")
        
        # List the screenshots
        if os.path.exists(screenshots_dir):
            print("\nScreenshot files:")
            for file in sorted(os.listdir(screenshots_dir)):
                if file.endswith('.png'):
                    print(f"  - {file}")
                    
    finally:
        driver.quit()

if __name__ == "__main__":
    main()