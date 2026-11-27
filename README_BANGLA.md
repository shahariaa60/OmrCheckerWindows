# ব্যবধান শিক্ষালয় — OMR Scanner Windows App (.exe)

## কীভাবে GitHub Actions দিয়ে Windows `.exe` বানাবেন (৩ মিনিটে)
1. GitHub-এ একটি নতুন ফ্রি রিপোজিটরি তৈরি করুন (যেমন: `byabodhan-omr-windows`)।
2. সাধারণ ফাইল হিসেবে শুধু **`ByabodhanOMR_WindowsApp.zip`** আপলোড করে **Commit changes** দিন।
3. **Add file > Create new file** চাপুন, নাম দিন `.github/workflows/build-windows-exe.yml` এবং ZIP-এর ভেতরের workflow কোডটি বসিয়ে **Commit changes** দিন।
4. **Actions** ট্যাবে ৩–৪ মিনিট পর সবুজ টিক এলে নিচে **Artifacts** থেকে **`Byabodhan-OMR-Windows-EXE`** ডাউনলোড করে আনজিপ করুন — ভেতরের `ByabodhanOMR_Windows.exe` ডাবল-ক্লিক করলেই চালু হবে!

## Google Sheet-এ ক্লাস অনুযায়ী আলাদা ট্যাব চালু করার নিয়ম
1. আপনার Google Sheet খুলে **Extensions > Apps Script**-এ যান।
2. `Code.gs` ফাইলের সম্পূর্ণ কোড পেস্ট করে **Save** দিন।
3. **Deploy > New deployment > Web app** (Execute as: Me, Who has access: Anyone) -> নতুন `/exec` URL-টি কপি করে Windows অ্যাপের **Apps Script URL** ঘরে বসিয়ে দিন।
