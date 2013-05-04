$(function() {
      $('#id_username').focus().end();

      // show message 
      var message = $('#message').text();
      $('#message').text('');
      if (message) {
	  $.growlUI(message);	  
	  console.log(message);      
      }

  });
