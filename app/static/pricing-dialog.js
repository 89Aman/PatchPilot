(function() {
  let lastOpener = null;

  function initDialog() {
    const dialog = document.getElementById("contactDialog");
    if (!dialog) return;

    const form = document.getElementById("contactForm");
    const errorBox = document.getElementById("dialogError");
    const recommendBanner = document.getElementById("recommendBanner");
    const successView = document.getElementById("dialogSuccess");
    const requestedInput = document.getElementById("requestedPlanInput");
    const titleEl = document.getElementById("dialogTitle");
    const teamSizeSelect = document.getElementById("contactTeamSize");
    const submitBtn = document.getElementById("submitBtn");

    function resetDialogState(planId) {
      if (form) {
        form.reset();
        form.style.display = "flex";
      }
      if (errorBox) {
        errorBox.textContent = "";
        errorBox.style.display = "none";
      }
      if (recommendBanner) {
        recommendBanner.style.display = "none";
      }
      if (successView) {
        successView.style.display = "none";
      }
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = "Submit Inquiry";
      }

      const planClean = (planId || "team").toLowerCase();
      if (requestedInput) requestedInput.value = planClean;
      if (titleEl) {
        titleEl.textContent = planClean === "enterprise" ? "Contact Enterprise Sales" : "Contact Team Sales";
      }

      // Sync cycle select with currently active page toggle
      const cycleSelect = document.getElementById("contactCycle");
      if (cycleSelect) {
        const activeCycleBtn = document.querySelector(".cycle-btn.active");
        if (activeCycleBtn && activeCycleBtn.dataset.cycle) {
          cycleSelect.value = activeCycleBtn.dataset.cycle;
        }
      }
    }

    function checkEnterpriseRecommendation() {
      if (!requestedInput || !teamSizeSelect || !recommendBanner) return;
      if (requestedInput.value === "team" && teamSizeSelect.value !== "2-5") {
        recommendBanner.style.display = "block";
      } else {
        recommendBanner.style.display = "none";
      }
    }

    teamSizeSelect?.addEventListener("change", checkEnterpriseRecommendation);

    window.openContactDialog = function(planId, openerEl) {
      lastOpener = openerEl || document.activeElement;
      resetDialogState(planId);
      checkEnterpriseRecommendation();
      if (typeof dialog.showModal === "function") {
        dialog.showModal();
      } else {
        dialog.setAttribute("open", "");
      }
    };

    window.closeContactDialog = function() {
      if (typeof dialog.close === "function") {
        dialog.close();
      } else {
        dialog.removeAttribute("open");
      }
      if (lastOpener && typeof lastOpener.focus === "function") {
        lastOpener.focus();
      }
    };

    dialog.addEventListener("cancel", function(e) {
      if (lastOpener && typeof lastOpener.focus === "function") {
        setTimeout(function() { lastOpener.focus(); }, 0);
      }
    });

    // Close on backdrop click
    dialog.addEventListener("click", function(e) {
      const rect = dialog.getBoundingClientRect();
      const inDialog = (
        rect.top <= e.clientY && e.clientY <= rect.top + rect.height &&
        rect.left <= e.clientX && e.clientX <= rect.left + rect.width
      );
      if (!inDialog) {
        window.closeContactDialog();
      }
    });

    form?.addEventListener("submit", async function(e) {
      e.preventDefault();
      errorBox.style.display = "none";
      errorBox.textContent = "";

      const name = (form.name.value || "").trim();
      const email = (form.email.value || "").trim();
      const company = (form.company_or_org.value || "").trim();
      const team_size = form.team_size.value;
      const billing_cycle = form.billing_cycle.value;
      const deployment_pref = form.deployment_pref.value;
      const message = (form.message.value || "").trim();
      const website = form.website.value;
      const requested_plan = requestedInput.value;

      if (!name || !email || !company) {
        errorBox.textContent = "Please fill in all required fields (Name, Email, and Company).";
        errorBox.style.display = "block";
        return;
      }

      submitBtn.disabled = true;
      submitBtn.textContent = "Submitting...";

      try {
        const resp = await fetch("/api/contact", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            requested_plan,
            name,
            email,
            company_or_org: company,
            team_size,
            billing_cycle,
            deployment_pref,
            message,
            website,
          }),
        });

        if (resp.status === 201 || resp.status === 200) {
          const resData = await resp.json();
          form.style.display = "none";
          recommendBanner.style.display = "none";
          const idDisplay = document.getElementById("inquiryIdDisplay");
          if (idDisplay) idDisplay.textContent = resData.id ? "#" + resData.id : "Received";
          successView.style.display = "flex";
        } else if (resp.status === 429) {
          errorBox.textContent = "Too many contact inquiries. Please try again later.";
          errorBox.style.display = "block";
          submitBtn.disabled = false;
          submitBtn.textContent = "Submit Inquiry";
        } else if (resp.status === 422) {
          const errData = await resp.json().catch(() => ({}));
          const msg = (errData.detail && errData.detail[0]?.msg) ? errData.detail[0].msg : "Please verify your input and email format.";
          errorBox.textContent = msg;
          errorBox.style.display = "block";
          submitBtn.disabled = false;
          submitBtn.textContent = "Submit Inquiry";
        } else {
          errorBox.textContent = "An error occurred while submitting your inquiry. Please try again or email us directly.";
          errorBox.style.display = "block";
          submitBtn.disabled = false;
          submitBtn.textContent = "Retry";
        }
      } catch (err) {
        errorBox.textContent = "Network error. Please check your connection and try again.";
        errorBox.style.display = "block";
        submitBtn.disabled = false;
        submitBtn.textContent = "Retry";
      }
    });

    // Check direct query parameter on load (e.g. /pricing?contact=team)
    const urlParams = new URLSearchParams(window.location.search);
    const contactParam = urlParams.get("contact");
    if (contactParam === "team" || contactParam === "enterprise") {
      window.openContactDialog(contactParam);
    }
  }

  // Delegated click handling on document
  document.addEventListener("click", function(e) {
    const openBtn = e.target.closest(".js-open-contact");
    if (openBtn) {
      e.preventDefault();
      const plan = openBtn.dataset.plan || "team";
      if (typeof window.openContactDialog === "function") {
        window.openContactDialog(plan, openBtn);
      }
      return;
    }

    const closeBtn = e.target.closest(".js-close-dialog");
    if (closeBtn) {
      e.preventDefault();
      if (typeof window.closeContactDialog === "function") {
        window.closeContactDialog();
      }
      return;
    }
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initDialog);
  } else {
    initDialog();
  }

  document.addEventListener("page:navigated", function() {
    initDialog();
  });
})();
